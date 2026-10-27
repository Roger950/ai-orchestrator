import argparse
import sys
from pathlib import Path

from .core import MODELS, Workspace, read_text


def parser():
    root = argparse.ArgumentParser(description="人工协作的多模型任务编排，无网络请求")
    root.add_argument("--root", type=Path, default=Path.cwd(), help="任务工作区，默认当前目录")
    commands = root.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="拆分任务并生成三份 Markdown 提示词")
    source = create.add_mutually_exclusive_group(required=True)
    source.add_argument("--description")
    source.add_argument("--file", type=Path)
    create.add_argument("--title", default="多模型协作任务")
    create.add_argument("--id")
    for name in ("status", "submitted", "import-results", "aggregate", "review", "finalize"):
        command = commands.add_parser(name)
        command.add_argument("task_id")
        if name in ("submitted", "import-results"):
            command.add_argument("--model", choices=MODELS, required=name == "submitted")
        if name in ("import-results", "review", "finalize"):
            command.add_argument("--file", type=Path, required=name in ("review", "finalize"))
        if name == "review":
            command.add_argument("--reviewer", required=True, help="实际审核者名称；工具不会自动进行语义审核")
            command.add_argument("--approve", action="store_true")
    commands.add_parser("list", help="刷新并列出任务状态")
    browser = commands.add_parser("browser", help="Chrome MCP 状态、提交确认与用户导出导入")
    browser.add_argument("action", choices=("status", "approve", "import", "failure", "candidates"))
    browser.add_argument("task_id")
    browser.add_argument("--model", choices=MODELS)
    browser.add_argument("--reviewer")
    browser.add_argument("--file", type=Path)
    browser.add_argument("--complete", action="store_true", help="实际确认导出回答完整")
    browser.add_argument("--method", choices=("user_copy", "user_export"), default="user_export")
    browser.add_argument("--reason")
    router = commands.add_parser("router", help="模型权限、路由建议和人工切换确认")
    router.add_argument("action", choices=("status", "plan", "confirm-switch"))
    router.add_argument("--task-id")
    router.add_argument("--platform", choices=MODELS)
    router.add_argument("--target")
    router.add_argument("--reasoning")
    router.add_argument("--reviewer")
    router.add_argument("--task-type", choices=("medical", "reasoning", "code", "long_text", "structured"))
    router.add_argument("--priority", choices=("quality", "balanced", "speed", "quota"), default="balanced")
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    workspace = Workspace(args.root)
    try:
        if args.command == "router":
            from .router import ModelRouter
            import json
            router = ModelRouter(workspace.root)
            if args.action == "status":
                print(router.report())
            else:
                if not args.task_id:
                    raise ValueError("--task-id required")
                workspace.load(args.task_id)
                if args.action == "plan":
                    print(json.dumps(router.plan(args.task_id, read_text(workspace.task_dir(args.task_id) / "description.md"), args.task_type, args.priority), ensure_ascii=False, indent=2))
                else:
                    if not args.platform or not args.target or not args.reasoning:
                        raise ValueError("--platform --target --reasoning required")
                    router.confirm_switch(args.task_id, args.platform, args.target, args.reasoning, args.reviewer or "")
                    print("已记录实际用户切换确认；仍需网页验证，未发送聊天")
        elif args.command == "browser":
            from .browser import BrowserWorkflow, generate_candidates
            flow = BrowserWorkflow(workspace, None)
            if args.action == "status":
                import json
                print(json.dumps(flow.load(args.task_id), ensure_ascii=False, indent=2))
            elif args.action == "candidates":
                print(generate_candidates(workspace, args.task_id))
            else:
                if not args.model:
                    raise ValueError("必须指定 --model")
                if args.action == "approve":
                    flow.approve(args.task_id, args.model, args.reviewer or "")
                    print("已记录本次网页提交确认；最终报告仍需单独审核")
                elif args.action == "import":
                    if not args.file:
                        raise ValueError("必须指定项目内 --file")
                    print(flow.import_file(args.task_id, args.model, args.file, args.complete, args.method))
                elif args.action == "failure":
                    flow.event(args.task_id, args.model, args.reason)
                    print("已记录失败；未重试提交")
        elif args.command == "create":
            description = read_text(args.file) if args.file else args.description
            print(workspace.create(description, args.title, args.id))
        elif args.command == "list":
            for path in sorted((workspace.root / "tasks").glob("*/state.json")):
                state = workspace.load(path.parent.name)
                print(f"{state['id']}\t{state['status']}\t{state['title']}")
        elif args.command == "status":
            workspace.load(args.task_id)
            print((workspace.task_dir(args.task_id) / "status.json").read_text(encoding="utf-8"))
        elif args.command == "submitted":
            workspace.submitted(args.task_id, args.model)
            print("已记录人工提交状态")
        elif args.command == "import-results":
            print(f"已导入 {workspace.import_results(args.task_id, args.model, args.file)} 份新结果")
        elif args.command == "aggregate":
            print(workspace.aggregate(args.task_id))
        elif args.command == "review":
            workspace.review(args.task_id, args.file, args.reviewer, args.approve)
            print("已保存审核意见：" + ("通过" if args.approve else "未通过"))
        elif args.command == "finalize":
            print(workspace.finalize(args.task_id, args.file))
        return 0
    except (OSError, ValueError, UnicodeError, KeyError) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 1
