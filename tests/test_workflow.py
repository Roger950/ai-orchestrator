import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from orchestrator.core import MODELS, SUMMARY_LIMIT, Workspace, extract_summary
from orchestrator.providers import Assignment, ManualProvider


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="协作测试-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = Workspace(self.root)
        self.task_id = self.workspace.create("设计文件备份方案，禁止实际删除文件。", "备份计划", "test-task")

    def result(self, model="chatgpt", content="## 摘要\n使用版本备份，先验证恢复。\n## 详细结果\n完整分析。"):
        path = self.workspace.result_path(self.task_id, model)
        path.write_text(content, encoding="utf-8")
        return path

    def all_results(self):
        for model in MODELS:
            self.result(model)
        self.assertEqual(self.workspace.import_results(self.task_id), 3)

    def source(self, name, text):
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return path

    def approve(self):
        self.workspace.review(self.task_id, self.source("review-source.md", "已核对约束；仍需实际恢复验证。"), "测试审核者", True)

    def test_creation_splits_roles_and_preserves_description(self):
        directory = self.workspace.task_dir(self.task_id)
        prompts = [(directory / f"{m}.md").read_text(encoding="utf-8") for m in MODELS]
        self.assertEqual(len(set(prompts)), 3)
        self.assertTrue(all("禁止实际删除文件" in p for p in prompts))
        self.assertTrue(all(not self.workspace.result_path(self.task_id, m).exists() for m in MODELS))

    def test_cli_end_to_end(self):
        def cli(*args):
            result = subprocess.run([sys.executable, "-X", "utf8", "-m", "orchestrator", "--root", str(self.root), *args],
                                    capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout
        cli("submitted", self.task_id, "--model", "chatgpt")
        for model in MODELS:
            self.result(model)
        self.assertIn("3", cli("import-results", self.task_id))
        cli("aggregate", self.task_id)
        review = self.source("审核源.md", "明确尚未实测，只批准方案稿。")
        cli("review", self.task_id, "--file", str(review), "--reviewer", "测试", "--approve")
        final = self.source("最终源.md", "最终方案：先备份，再进行恢复演练。")
        cli("finalize", self.task_id, "--file", str(final))
        state = json.loads(cli("status", self.task_id))
        self.assertEqual(state["status"], "completed")
        self.assertIn("最终方案", (self.root / state["final"]["path"]).read_text(encoding="utf-8"))

    def test_partial_result_blocks_aggregate_and_review(self):
        self.result()
        self.workspace.import_results(self.task_id)
        with self.assertRaisesRegex(ValueError, "claude, gemini"):
            self.workspace.aggregate(self.task_id)
        with self.assertRaises(ValueError):
            self.approve()

    def test_finalize_requires_current_approval(self):
        self.all_results()
        source = self.source("final.md", "方案稿")
        with self.assertRaises(ValueError):
            self.workspace.finalize(self.task_id, source)
        self.workspace.review(self.task_id, source, "测试", False)
        with self.assertRaises(ValueError):
            self.workspace.finalize(self.task_id, source)

    def test_changed_result_invalidates_approval_and_final(self):
        self.all_results()
        self.approve()
        final = self.workspace.finalize(self.task_id, self.source("final.md", "最终稿"))
        self.result(content="## 摘要\n修改了结论。")
        state = self.workspace.load(self.task_id)
        self.assertEqual(state["models"]["chatgpt"]["status"], "needs_reimport")
        self.assertIsNone(state["review"])
        self.assertIsNone(state["final"])
        self.assertTrue(final.exists())  # historical snapshot remains
        self.assertEqual(self.workspace.import_results(self.task_id), 1)

    def test_deleted_result_invalidates_import(self):
        self.all_results()
        self.workspace.result_path(self.task_id, "gemini").unlink()
        self.assertEqual(self.workspace.load(self.task_id)["models"]["gemini"]["status"], "needs_reimport")

    def test_summary_tamper_invalidates_review(self):
        self.all_results()
        self.approve()
        (self.workspace.task_dir(self.task_id) / "summaries" / "claude.md").write_text("被修改", encoding="utf-8")
        self.assertIsNone(self.workspace.load(self.task_id)["review"])

    def test_review_tamper_invalidates_approval(self):
        self.all_results()
        self.approve()
        (self.workspace.task_dir(self.task_id) / "review.md").write_text("不同意见", encoding="utf-8")
        self.assertIsNone(self.workspace.load(self.task_id)["review"])

    def test_final_tamper_clears_current_pointer(self):
        self.all_results()
        self.approve()
        final = self.workspace.finalize(self.task_id, self.source("final.md", "最终稿"))
        final.write_text("被修改", encoding="utf-8")
        self.assertIsNone(self.workspace.load(self.task_id)["final"])

    def test_noop_import_keeps_approval(self):
        self.all_results()
        self.approve()
        self.assertEqual(self.workspace.import_results(self.task_id), 0)
        self.assertTrue(self.workspace.load(self.task_id)["review"]["approved"])

    def test_external_utf8_bom_import(self):
        path = self.root / "外部回答.md"
        path.write_text("## 摘要\n外部回答", encoding="utf-8-sig")
        self.assertEqual(self.workspace.import_results(self.task_id, "gemini", path), 1)
        self.assertIn("外部回答", self.workspace.result_path(self.task_id, "gemini").read_text(encoding="utf-8"))

    def test_invalid_batch_does_not_partly_import(self):
        self.result()
        self.result("claude", "  ")
        with self.assertRaises(ValueError):
            self.workspace.import_results(self.task_id)
        self.assertEqual(self.workspace.load(self.task_id)["models"]["chatgpt"]["status"], "awaiting_manual_submission")

    def test_missing_summary_and_truncation_are_visible(self):
        summary, warning = extract_summary("长" * (SUMMARY_LIMIT + 100))
        self.assertIn("截断", warning)
        self.assertIn("未找到", warning)
        self.assertLess(len(summary), SUMMARY_LIMIT + 100)
        summary, warning = extract_summary("## 摘要\n简短结论\n## 详细结果\n不应进入摘要")
        self.assertEqual(summary, "简短结论")
        self.assertIsNone(warning)

    def test_task_ids_cannot_escape_workspace_or_overwrite(self):
        for task_id in ("../escape", "..", "a/b", "C:\\escape", ""):
            with self.subTest(task_id=task_id), self.assertRaises(ValueError):
                self.workspace.task_dir(task_id)
        with self.assertRaises(ValueError):
            self.workspace.create("新任务", "重复", self.task_id)

    def test_manual_provider_never_claims_submission(self):
        assignment = Assignment(self.task_id, "chatgpt", self.workspace.task_dir(self.task_id) / "chatgpt.md",
                                self.workspace.result_path(self.task_id, "chatgpt"))
        provider = ManualProvider()
        receipt = provider.dispatch(assignment)
        self.assertEqual(receipt.status, "awaiting_manual_submission")
        self.assertIsNone(provider.collect(assignment, receipt))
        self.result()
        self.assertIn("摘要", provider.collect(assignment, receipt))

    def test_changed_task_goal_cannot_reuse_old_review(self):
        self.all_results()
        self.approve()
        (self.workspace.task_dir(self.task_id) / "description.md").write_text("新的目标", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "新 ID"):
            self.workspace.load(self.task_id)


if __name__ == "__main__":
    unittest.main()
