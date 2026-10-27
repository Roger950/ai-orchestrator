"""Run an offline demo with explicitly simulated model responses."""

import argparse
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from orchestrator.core import MODELS, Workspace, read_text  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="离线模拟示例；不会调用任何模型")
    parser.add_argument("--root", type=Path, default=PROJECT)
    parser.add_argument("--id", help="指定任务 ID；省略时生成新 ID，拒绝覆盖已有任务")
    args = parser.parse_args()
    workspace = Workspace(args.root)
    examples = PROJECT / "examples"
    task_id = workspace.create(read_text(examples / "task.md"), "Windows 文件备份方案（模拟示例）", args.id)
    for model in MODELS:
        workspace.submitted(task_id, model)  # simulate the manual handoff step
        assert workspace.import_results(task_id, model, examples / "responses" / f"{model}.md") == 1
    aggregation = workspace.aggregate(task_id)
    workspace.review(task_id, examples / "review.md", "Codex（模拟示例审核）", True)
    final = workspace.finalize(task_id, examples / "final-draft.md")
    state = workspace.load(task_id)
    if state["status"] != "completed" or not final.exists() or not aggregation.exists():
        raise RuntimeError("示例流程未完成")
    print(f"模拟示例已完成：{task_id}")
    print(f"简报：{workspace.task_dir(task_id) / 'brief.md'}")
    print(f"汇总：{aggregation}")
    print(f"最终稿：{final}")
    print("未调用模型、未提交网页、未执行系统备份。")


if __name__ == "__main__":
    main()
