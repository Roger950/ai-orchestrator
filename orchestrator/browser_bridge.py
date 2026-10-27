"""File-based RPC to the MCP tools already owned by the current Codex session.

No MCP config, credentials, remote-debugging ports or browser profiles are read.
Run via `python -m orchestrator.browser_bridge --help`. See BROWSER_WORKFLOW.md.
"""
import argparse
import json
import time
import uuid
from pathlib import Path

from .browser import BrowserWorkflow
from .core import MODELS, Workspace, json_write

ALLOWED = {"list_pages", "take_snapshot", "fill", "evaluate_script", "click", "wait_for", "navigate_page"}


class Mailbox:
    def __init__(self, directory, timeout=300):
        self.directory = directory
        self.timeout = timeout
        directory.mkdir(parents=True, exist_ok=True)

    def __call__(self, name, arguments):
        if name not in ALLOWED:
            raise ValueError("Tool not allowed")
        request_id = uuid.uuid4().hex
        request = self.directory / "request.json"
        response = self.directory / f"response-{request_id}.json"
        json_write(request, {"id": request_id, "tool": name, "arguments": arguments,
                             "expires_at": time.time() + self.timeout})
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if response.exists():
                try:
                    data = json.loads(response.read_text(encoding="utf-8-sig"))
                finally:
                    response.unlink()
                if data.get("id") != request_id:
                    raise ValueError("Mismatched MCP response")
                request.unlink(missing_ok=True)
                if "error" in data:
                    raise RuntimeError(data["error"])
                return data["result"]
            time.sleep(0.1)
        request.unlink(missing_ok=True)
        raise TimeoutError("Codex MCP bridge response timed out; no automatic retry")


def main():
    p = argparse.ArgumentParser(description="Codex existing Chrome MCP disk bridge")
    p.add_argument("action", choices=("discover", "fill", "submit", "local-run", "router-discover", "router-lock", "router-switch", "router-local", "router-verify", "local-failure-checks"))
    p.add_argument("task_id")
    p.add_argument("--root", type=Path, default=Path.cwd())
    p.add_argument("--model", choices=MODELS)
    p.add_argument("--fixture", type=Path)
    p.add_argument("--target")
    p.add_argument("--reasoning")
    p.add_argument("--task-type", choices=("medical", "reasoning", "code", "long_text", "structured"))
    args = p.parse_args()
    ws = Workspace(args.root)
    ws.load(args.task_id)
    run = uuid.uuid4().hex[:12]
    directory = ws.root / "logs" / args.task_id / ("bridge-" + run)
    json_write(ws.task_dir(args.task_id) / "active-bridge.json", {"directory": str(directory), "run": run})
    fixture = args.fixture.resolve() if args.fixture else None
    if fixture and (not fixture.is_relative_to(ws.root) or not fixture.is_file()):
        p.error("Fixture must exist inside the project")
    mailbox = Mailbox(directory)
    browser = BrowserWorkflow(ws, mailbox, fixture.as_uri() if fixture else None)
    try:
        if args.action == "local-failure-checks":
            if not fixture:
                raise ValueError("必须指定本地 --fixture")
            from examples.browser_acceptance import verify_local_failures
            result = verify_local_failures(ws, args.task_id, mailbox, fixture)
        elif args.action.startswith("router-"):
            from .router import ModelRouter
            from .core import read_text
            policy = ws.root / "logs" / args.task_id / "model-policy-fixture.json" if fixture else None
            router = ModelRouter(ws.root, mailbox, policy, fixture.as_uri() if fixture else None)
            browser.router = router
            if args.action == "router-verify":
                pages = browser.load(args.task_id)["models"]
                for model, pp in router.load()["platforms"].items():
                    observation = pp.get("last_observation", {})
                    if not observation.get("actual"):
                        continue
                    page = {k: pages[model][k] for k in ("pageId", "url", "model")}
                    router.lock(args.task_id, model, observation["actual"], observation.get("reasoning_level"), page)
                result = router.load()["locks"].get(args.task_id, {})
                if not fixture:
                    router.report()
            elif args.action in {"router-discover", "router-local"}:
                pages = browser.discover(args.task_id)["models"]
                for model, page in pages.items():
                    if page["status"] in {"ready", "imported"}:
                        router.observe(model, {k: page[k] for k in ("pageId", "url", "model")}, open_selector=True)
                result = router.plan(args.task_id, read_text(ws.task_dir(args.task_id) / "description.md"), args.task_type)
                if not fixture:
                    router.report()
                if args.action == "router-local":
                    if not fixture:
                        raise ValueError("router-local requires local --fixture")
                    for model in MODELS:
                        record = browser.load(args.task_id)["models"][model]
                        if record["status"] == "imported":
                            continue
                        if record.get("submitted_at"):
                            if not browser.collect_local(args.task_id, model):
                                raise ValueError("本地结果不完整：" + model)
                            continue
                        choices = result["platforms"][model]["ranked"]
                        if not choices:
                            raise ValueError("没有已验证模型：" + model)
                        choice = choices[0]
                        page = {k: pages[model][k] for k in ("pageId", "url", "model")}
                        lock = router.switch(args.task_id, model, choice["model"], choice["reasoning_level"], page)
                        if lock["lock_status"] != "locked":
                            raise ValueError("本地模型锁定失败：" + model)
                        browser.fill(args.task_id, model)
                        browser.submit(args.task_id, model, local_auto=True)
                        if not browser.collect_local(args.task_id, model):
                            raise ValueError("本地结果不完整：" + model)
                    result = {"status": "router_and_browser_verified", "synthetic": True}
            else:
                if not args.model or not args.target or not args.reasoning:
                    raise ValueError("--model --target --reasoning required")
                page = browser.load(args.task_id)["models"][args.model]
                page = {k: page[k] for k in ("pageId", "url", "model")}
                action = router.lock if args.action == "router-lock" else router.switch
                result = action(args.task_id, args.model, args.target, args.reasoning, page)
        elif args.action == "discover":
            result = browser.discover(args.task_id)
        elif args.action == "local-run":
            if not fixture:
                p.error("local-run requires --fixture")
            browser.discover(args.task_id)
            for model in MODELS:
                record = browser.load(args.task_id)["models"][model]
                if record["status"] == "imported":
                    continue
                if not record.get("submitted_at"):
                    browser.fill(args.task_id, model)
                    browser.submit(args.task_id, model, local_auto=True)
                if not browser.collect_local(args.task_id, model):
                    raise ValueError("本地结果不完整：" + model)
            result = {"status": "local_flow_verified", "synthetic": True}
        else:
            if not args.model:
                p.error("--model required")
            result = getattr(browser, args.action)(args.task_id, args.model)
        json_write(directory / "done.json", {"ok": True, "result": result})
    except Exception as error:
        json_write(directory / "done.json", {"ok": False, "error": str(error)})
        raise


if __name__ == "__main__":
    main()
