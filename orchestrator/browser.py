"""Chrome MCP workflow. No browser connection/configuration or model API client.

The caller injects call_tool(name, arguments). Codex supplies its existing MCP
tools through the disk mailbox in browser_bridge.py. Never connect to CDP here.
"""
import json
import re
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from .core import MODELS, Workspace, digest, json_write, now, read_text, write

HOSTS = {"chatgpt.com": "chatgpt", "chat.openai.com": "chatgpt",
         "claude.ai": "claude", "gemini.google.com": "gemini"}
FAILURES = {"login_expired", "page_refreshed", "incomplete", "timeout",
            "missing_result", "tool_error", "ambiguous_page", "unavailable"}


def tool_text(result):
    if isinstance(result, str):
        return result
    text = "\n".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
    if result.get("isError"):
        raise RuntimeError("Chrome MCP: " + text[:500])
    return text


def identify(url, fixture_url=None):
    p = urlsplit(url)
    if p.scheme == "https" and p.hostname in HOSTS and not p.username and not p.password:
        return HOSTS[p.hostname]
    # Local automation is restricted to one explicitly configured fixture file.
    if fixture_url and p.scheme == "file" and p._replace(query="", fragment="").geturl() == fixture_url:
        model = parse_qs(p.query).get("model", [None])[0]
        return model if model in MODELS else None
    return None


def pages_from_text(text, fixture_url=None):
    pages = []
    for line in text.splitlines():
        match = re.match(r"^(\d+): .*\((\S+)\)(?: \[selected\])?$", line)
        if match:
            page_id, url = match.groups()
            model = identify(url, fixture_url)
            if model:
                pages.append({"pageId": int(page_id), "url": url, "model": model})
    return pages


def script_value(text):
    fenced = re.search(r"```(?:json)?\s*\n([\s\S]*?)\n```", text)
    return json.loads(fenced.group(1) if fenced else text)


def controls(snapshot):
    """Discard chat text; persist only the exact controls needed for input.

    Snapshot UIDs are ephemeral. Resolve again before every browser operation.
    Ambiguous composers/buttons must be resolved by the human, never by guessing.
    """
    inputs, buttons = [], []
    login = False
    for line in snapshot.splitlines():
        if re.search(r'(?:button|link) "(?:Log in|Sign in|登录|登入)"', line, re.I):
            login = True
        m = re.search(r'uid=(\S+)\s+(textbox|combobox|button)\s+"([^"]*)"(.*)', line)
        if not m:
            continue
        uid, role, name, flags = m.groups()
        if "disabled" in flags:
            continue
        if role == "textbox" and re.search(r"prompt|message|ask|reply|输入|询问|提问|消息|回复|和.*对话", name, re.I):
            inputs.append({"uid": uid, "role": role, "name": name})
        if role == "button" and re.fullmatch(r"send(?: message| prompt)?|submit|发送(?:消息|提示)?|提交", name, re.I):
            buttons.append({"uid": uid, "role": role, "name": name})
    return {"inputs": inputs, "buttons": buttons, "login_visible": login}


class BrowserWorkflow:
    def __init__(self, workspace, call_tool, fixture_url=None, router=None):
        self.ws = workspace
        self.call_tool = call_tool
        self.fixture_url = fixture_url
        self.router = router

    def call(self, name, **arguments):
        try:
            return tool_text(self.call_tool(name, arguments))
        except RuntimeError as error:
            if name == "wait_for" and re.search(r"timeout|timed out", str(error), re.I):
                raise TimeoutError(str(error)) from error
            raise

    def path(self, task_id):
        return self.ws.task_dir(task_id) / "browser.json"

    def load(self, task_id):
        state = self.ws.load(task_id)
        candidates = self.ws.final_dir(task_id) / "candidates.json"
        if candidates.exists():
            metadata = json.loads(candidates.read_text(encoding="utf-8"))
            metadata["current"] = (metadata.get("snapshot") == self.ws.snapshot(state)
                                   and all(r["status"] == "imported" for r in state["models"].values()))
            json_write(candidates, metadata)
        path = self.path(task_id)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"models": {}}

    def save(self, task_id, data):
        json_write(self.path(task_id), data)
        # Compact state is kept separate from core imported-result validity.
        state = self.ws.load(task_id)
        for model, record in data["models"].items():
            state["models"][model]["browser_status"] = record["status"]
        self.ws.save(state)

    def event(self, task_id, model, status, detail=""):
        if status not in FAILURES:
            raise ValueError("Unknown failure status")
        data = self.load(task_id)
        rec = data["models"].setdefault(model, {})
        rec.update(status=status, detail=detail, updated_at=now())
        rec.pop("approval", None)
        self.save(task_id, data)

    def snapshot(self, page_id):
        return controls(self.call("take_snapshot", pageId=page_id, verbose=False))

    def discover(self, task_id):
        data = self.load(task_id)
        pages = pages_from_text(self.call("list_pages"), self.fixture_url)
        if self.fixture_url:
            pages = [p for p in pages if urlsplit(p["url"]).scheme == "file"]
        for model in MODELS:
            matches = [p for p in pages if p["model"] == model]
            old = data["models"].get(model, {})
            if len(matches) != 1:
                data["models"][model] = {"status": "ambiguous_page" if matches else "unavailable",
                                          "candidates": matches}
                continue
            page = matches[0]
            # Local runs must never inspect a consumer page.
            if self.fixture_url and urlsplit(page["url"]).scheme != "file":
                data["models"][model] = {"status": "unavailable", "detail": "fixture mode"}
                continue
            try:
                adapter = self.snapshot(page["pageId"])
                available = len(adapter["inputs"]) == 1
                status = "ready" if available else ("login_expired" if adapter["login_visible"] else "unavailable")
                # Discovery cannot reset an uncertain submission into a safe-to-retry state.
                if old.get("attempted_at") or old.get("status") in {"submitted", "submitting", "timeout", "incomplete", "missing_result", "imported"}:
                    status = old["status"]
                data["models"][model] = {**old, **page, "adapter": adapter, "status": status,
                                          "checked_at": now()}
                data["models"][model].pop("approval", None)
            except (RuntimeError, OSError, TimeoutError) as error:
                data["models"][model] = {**old, **page, "status": "tool_error", "detail": str(error)}
        self.save(task_id, data)
        return data

    def current(self, record, require_button=False):
        pages = pages_from_text(self.call("list_pages"), self.fixture_url)
        if not any(p["pageId"] == record["pageId"] and p["url"] == record["url"] for p in pages):
            raise ValueError("page_refreshed")
        adapter = self.snapshot(record["pageId"])
        if len(adapter["inputs"]) != 1 or (require_button and len(adapter["buttons"]) != 1):
            raise ValueError("login_expired" if adapter["login_visible"] else "page_refreshed")
        return adapter

    def fill(self, task_id, model):
        data = self.load(task_id)
        record = data["models"][model]
        if record.get("attempted_at"):
            raise ValueError("已有提交尝试，先人工核对页面；重新发送请创建新任务，避免重复提交")
        if record["status"] not in {"ready", "awaiting_confirmation", "page_refreshed", "login_expired", "unavailable"}:
            raise ValueError("必须先检查页面；不自动重试已提交或状态不明的请求")
        try:
            adapter = self.current(record)
            prompt = self.ws.task_dir(task_id) / f"{model}.md"
            self.call("fill", pageId=record["pageId"], uid=adapter["inputs"][0]["uid"], value=read_text(prompt))
            observed = script_value(self.call("evaluate_script", pageId=record["pageId"],
                args=[adapter["inputs"][0]["uid"]],
                function="(el) => ({value: el.value ?? el.innerText, epoch: performance.timeOrigin})", waitForStableDom=False))
            if observed["value"] != read_text(prompt):
                raise ValueError("page_refreshed")
            record.update(status="awaiting_confirmation", prompt_sha256=digest(prompt), filled_at=now(), epoch=observed["epoch"])
            record.pop("approval", None)
            self.save(task_id, data)
            return {"model": model, "pageId": record["pageId"], "url": record["url"],
                    "prompt": str(prompt), "sha256": record["prompt_sha256"], "status": record["status"]}
        except (OSError, RuntimeError, TimeoutError, ValueError) as error:
            self.event(task_id, model, str(error) if str(error) in FAILURES else "tool_error", str(error))
            raise

    def approve(self, task_id, model, reviewer):
        if not reviewer.strip():
            raise ValueError("必须填写实际确认者")
        data = self.load(task_id)
        rec = data["models"][model]
        if rec["status"] != "awaiting_confirmation":
            raise ValueError("必须先填入并预览本次提示词")
        if digest(self.ws.task_dir(task_id) / f"{model}.md") != rec["prompt_sha256"]:
            raise ValueError("提示词已改变，请重新填入")
        rec["approval"] = {"reviewer": reviewer, "at": now(), "sha256": rec["prompt_sha256"]}
        self.save(task_id, data)

    def submit(self, task_id, model, local_auto=False):
        data = self.load(task_id)
        rec = data["models"][model]
        local = bool(self.fixture_url and identify(rec["url"], self.fixture_url) == model
                     and urlsplit(rec["url"]).scheme == "file")
        if local_auto and not local:
            raise ValueError("自动提交只允许显式配置的本地测试页面")
        if rec["status"] != "awaiting_confirmation" or (not local_auto and not rec.get("approval")):
            raise ValueError("真实网页提交前必须取得本次人工确认")
        if digest(self.ws.task_dir(task_id) / f"{model}.md") != rec["prompt_sha256"]:
            raise ValueError("提示词变化，旧确认无效")
        try:
            lock = None
            if self.router:
                lock = self.router.require_lock(task_id, model)
                if lock["page"] != {"pageId": rec["pageId"], "url": rec["url"], "model": model}:
                    raise ValueError("Model Router 锁定页面与提交页面不一致")
            elif not local:
                from .router import ModelRouter
                router = ModelRouter(self.ws.root, self.call_tool)
                lock = router.require_lock(task_id, model)
                if lock["page"]["pageId"] != rec["pageId"] or lock["page"]["url"] != rec["url"]:
                    raise ValueError("Model Router 锁定页面与提交页面不一致")
            adapter = self.current(rec, require_button=True)
            # Read only the already identified input, never chat output.
            value = script_value(self.call("evaluate_script", pageId=rec["pageId"], args=[adapter["inputs"][0]["uid"]],
                              function="(el) => ({value: el.value ?? el.innerText, epoch: performance.timeOrigin})", waitForStableDom=False))
            prompt = read_text(self.ws.task_dir(task_id) / f"{model}.md")
            if value["value"] != prompt or value["epoch"] != rec["epoch"]:
                raise ValueError("page_refreshed")
            rec.update(status="submitting", approval=None, attempted_at=now())
            if lock:
                rec["model_lock_at_submission"] = lock
            self.save(task_id, data)  # consume permission BEFORE side effects
            self.call("click", pageId=rec["pageId"], uid=adapter["buttons"][0]["uid"])
            rec.update(status="submitted", submitted_at=now())
            self.save(task_id, data)
            state = self.ws.load(task_id)
            if state["models"][model]["status"] == "awaiting_manual_submission":
                self.ws.submitted(task_id, model)
        except (RuntimeError, OSError, TimeoutError, ValueError) as error:
            self.event(task_id, model, str(error) if str(error) in FAILURES else "tool_error", str(error))
            raise

    def collect_local(self, task_id, model, timeout=5000):
        data = self.load(task_id)
        rec = data["models"][model]
        if not self.fixture_url or urlsplit(rec["url"]).scheme != "file" or identify(rec["url"], self.fixture_url) != model:
            raise ValueError("真实网页仅接受用户通过允许的复制/导出功能取得的文件")
        if rec["status"] not in {"submitted", "timeout", "incomplete", "missing_result"} or not rec.get("submitted_at"):
            raise ValueError("本地页面尚未提交")
        try:
            self.current(rec)
            self.call("wait_for", pageId=rec["pageId"], text=["LOCAL_RESULT_COMPLETE"], timeout=timeout, visible=True)
            # Local fixture only. Consumer output evaluation is intentionally not implemented.
            destination = self.ws.root / "logs" / task_id / f"{model}-local-capture.json"
            raw = self.call("evaluate_script", pageId=rec["pageId"],
                      function="() => ({complete: document.querySelector('#result').dataset.complete === 'true', text: document.querySelector('#result').textContent})",
                      waitForStableDom=False)
            # Some MCP servers restrict their own filePath roots independently of
            # Codex. Persist through Python inside this project, without reconfiguration.
            write(destination, raw)
            # The actual tool writes JSON or a fenced JSON response depending on server version.
            match = re.search(r'\{\s*"complete"[\s\S]*\}', raw)
            result = json.loads(match.group(0)) if match else {}
            source = destination.with_suffix(".md")
            write(source, result.get("text", ""))
            return self.import_file(task_id, model, source, result.get("complete") is True, "local_fixture")
        except (TimeoutError, RuntimeError, OSError, ValueError) as error:
            self.event(task_id, model, "timeout" if isinstance(error, TimeoutError) else "tool_error", str(error))
            raise

    def import_file(self, task_id, model, source, complete, method="user_export"):
        if model not in MODELS or method not in {"user_copy", "user_export", "local_fixture"}:
            raise ValueError("不支持的模型或导入来源")
        if not complete:
            self.event(task_id, model, "incomplete", "需用户确认完整结果后重新导入")
            return False
        source = Path(source).resolve()
        if not source.is_relative_to(self.ws.root):
            raise ValueError("请先将导出文件放入项目目录")
        if not source.exists():
            self.event(task_id, model, "missing_result", "指定导出文件不存在")
            return False
        try:
            read_text(source)
            self.ws.import_results(task_id, model, source)
        except (ValueError, UnicodeError):
            self.event(task_id, model, "incomplete", "空白、过大或非 UTF-8 结果")
            return False
        data = self.load(task_id)
        data["models"].setdefault(model, {}).update(status="imported", method=method,
                                                   imported_at=now(), sha256=digest(self.ws.result_path(task_id, model)))
        data["models"][model].pop("detail", None)
        provenance_path = self.ws.root / "results" / task_id / "sources.json"
        provenance = json.loads(provenance_path.read_text(encoding="utf-8")) if provenance_path.exists() else {}
        provenance[model] = {"method": method, "complete_attested": True,
            "sha256": digest(self.ws.result_path(task_id, model)), "imported_at": now(),
            "model_lock_at_submission": data["models"][model].get("model_lock_at_submission"),
            "attribution": "用户导入文件；界面锁定记录不等于对文件内容来源的独立证明"}
        json_write(provenance_path, provenance)
        self.save(task_id, data)
        generate_candidates(self.ws, task_id)
        return True


def generate_candidates(ws, task_id):
    state = ws.load(task_id)
    if not all(r["status"] == "imported" for r in state["models"].values()):
        return False
    ws.aggregate(task_id)
    snapshot = json.dumps(ws.snapshot(state), ensure_ascii=False)
    brief = (ws.task_dir(task_id) / "brief.md").read_text(encoding="utf-8")
    header = f"<!-- source_sha256={snapshot} -->\n\n未经人工批准的候选稿。不是自动事实核验或分歧裁决。\n\n"
    write(ws.final_dir(task_id) / "cross-review-draft.md", header +
          "# 交叉审核草稿\n\n对照 description.md 与三份原文填写：\n"
          "- 一致结论：[待审核]\n- 分歧与证据对照：[待审核]\n- 缺失证据与验收：[待审核]\n"
          "- 审核结论：[待人工决定]\n\n" + brief)
    write(ws.final_dir(task_id) / "report-candidate.md", header +
          "# 最终报告候选稿\n\n以下为按角色组织的摘要材料，需审核者综合改写后批准。\n\n" + brief)
    json_write(ws.final_dir(task_id) / "candidates.json", {"snapshot": ws.snapshot(state), "at": now(), "approved": False, "current": True})
    return True
