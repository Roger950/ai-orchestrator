import json
import tempfile
import unittest
from pathlib import Path

from orchestrator.browser import BrowserWorkflow, controls, identify, pages_from_text
from orchestrator.core import MODELS, Workspace, digest


class FakeChrome:
    """Deterministic transport double; live MCP is separately tested on fixture."""
    def __init__(self, fixture):
        self.fixture = fixture
        self.calls = []
        self.values = {}
        self.login = False
        self.refresh = False
        self.fail_click = False
        self.timeout = False
        self.complete = True
        self.serial = 0
        self.epoch = 1

    def __call__(self, name, args):
        self.calls.append((name, args))
        page = args.get('pageId', 1)
        text = ''
        if name == 'list_pages':
            text = '\n'.join(f'{i}: test ({self.fixture}?model={m})' for i, m in enumerate(MODELS, 1))
        elif name == 'take_snapshot':
            self.serial += 1
            if self.login:
                text = 'uid=1_1 button "Log in"'
            else:
                text = f'uid={self.serial}_1 textbox "Message"\nuid={self.serial}_2 button "Send"\nuid=x StaticText "PRIVATE OUTPUT"'
        elif name == 'fill':
            self.values[page] = args['value']
        elif name == 'click' and self.fail_click:
            raise RuntimeError('uncertain click')
        elif name == 'wait_for' and self.timeout:
            raise TimeoutError('local wait timeout')
        elif name == 'evaluate_script':
            if 'complete:' in args['function']:
                text = json.dumps({'complete': self.complete, 'text': '## 摘要\n本地模拟完整输出\n## 详细结果\n测试'})
            else:
                text = json.dumps({'value': '' if self.refresh else self.values.get(page, ''),
                                   'epoch': self.epoch}, ensure_ascii=False)
        return {'content': [{'type': 'text', 'text': text}]}


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='browser-')
        self.addCleanup(self.tmp.cleanup)
        self.ws = Workspace(self.tmp.name)
        self.task = self.ws.create('设计备份与恢复方案', '本地测试', 'browser-test')
        self.fixture = (self.ws.root / 'fixture.html').as_uri()
        self.chrome = FakeChrome(self.fixture)
        self.flow = BrowserWorkflow(self.ws, self.chrome, self.fixture)
        self.flow.discover(self.task)

    def fill(self):
        self.flow.fill(self.task, 'chatgpt')

    def test_domain_matching_rejects_spoof_and_non_https(self):
        for url in ('https://chatgpt.com.evil.test/', 'https://evil.test/?chatgpt.com', 'http://claude.ai/', 'file:///claude.ai'):
            self.assertIsNone(identify(url))
        self.assertEqual(identify('https://gemini.google.com/app'), 'gemini')

    def test_snapshot_only_retains_controls(self):
        result = self.flow.load(self.task)
        self.assertNotIn('PRIVATE OUTPUT', json.dumps(result))
        self.assertEqual(len(result['models']), 3)

    def test_duplicate_pages_are_not_guessed(self):
        text = '1: A (https://chatgpt.com/)\n2: B (https://chatgpt.com/c/abc)'
        flow = BrowserWorkflow(self.ws, lambda n, a: text)
        self.assertEqual(flow.discover(self.task)['models']['chatgpt']['status'], 'ambiguous_page')

    def test_fill_does_not_submit_and_uses_new_snapshot(self):
        self.fill()
        self.assertFalse(any(n == 'click' for n, a in self.chrome.calls))
        self.assertEqual(self.flow.load(self.task)['models']['chatgpt']['status'], 'awaiting_confirmation')
        fill = next(a for n, a in self.chrome.calls if n == 'fill')
        self.assertTrue(fill['uid'].startswith('4_'))

    def test_submission_requires_confirmation(self):
        self.fill()
        with self.assertRaises(ValueError):
            self.flow.submit(self.task, 'chatgpt')
        self.flow.approve(self.task, 'chatgpt', '实际审核者')
        self.flow.submit(self.task, 'chatgpt')
        with self.assertRaises(ValueError):
            self.flow.submit(self.task, 'chatgpt')

    def test_changed_prompt_invalidates_confirmation(self):
        self.fill()
        self.flow.approve(self.task, 'chatgpt', '用户')
        (self.ws.task_dir(self.task) / 'chatgpt.md').write_text('changed', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.flow.submit(self.task, 'chatgpt')

    def test_refresh_detected_before_click(self):
        self.fill()
        self.chrome.refresh = True
        with self.assertRaises(ValueError):
            self.flow.submit(self.task, 'chatgpt', local_auto=True)
        self.assertEqual(self.flow.load(self.task)['models']['chatgpt']['status'], 'page_refreshed')
        self.assertFalse(any(n == 'click' for n, a in self.chrome.calls))

    def test_login_expiry(self):
        self.chrome.login = True
        self.assertEqual(self.flow.discover(self.task)['models']['chatgpt']['status'], 'login_expired')

    def test_uncertain_click_never_retried(self):
        self.fill()
        self.chrome.fail_click = True
        with self.assertRaises(RuntimeError):
            self.flow.submit(self.task, 'chatgpt', local_auto=True)
        self.flow.discover(self.task)
        with self.assertRaises(ValueError):
            self.flow.fill(self.task, 'chatgpt')
        with self.assertRaises(ValueError):
            self.flow.submit(self.task, 'chatgpt', local_auto=True)

    def test_real_page_cannot_auto_submit_or_collect(self):
        data = self.flow.load(self.task)
        data['models']['chatgpt']['url'] = 'https://chatgpt.com/'
        self.flow.save(self.task, data)
        with self.assertRaises(ValueError):
            self.flow.submit(self.task, 'chatgpt', local_auto=True)
        with self.assertRaises(ValueError):
            self.flow.collect_local(self.task, 'chatgpt')

    def test_incomplete_and_missing_do_not_import(self):
        source = self.ws.root / 'missing.md'
        self.assertFalse(self.flow.import_file(self.task, 'chatgpt', source, False))
        self.assertFalse(self.flow.import_file(self.task, 'chatgpt', source, True))
        self.assertFalse(self.ws.result_path(self.task, 'chatgpt').exists())

    def test_timeout_recorded(self):
        self.fill()
        self.flow.submit(self.task, 'chatgpt', local_auto=True)
        self.chrome.timeout = True
        with self.assertRaises(TimeoutError):
            self.flow.collect_local(self.task, 'chatgpt')
        self.assertEqual(self.flow.load(self.task)['models']['chatgpt']['status'], 'timeout')

    def test_complete_local_flow_and_candidates_require_final_review(self):
        for model in MODELS:
            self.flow.fill(self.task, model)
            self.flow.submit(self.task, model, local_auto=True)
            self.assertTrue(self.flow.collect_local(self.task, model))
        self.assertTrue((self.ws.final_dir(self.task) / 'cross-review-draft.md').exists())
        self.assertTrue((self.ws.final_dir(self.task) / 'report-candidate.md').exists())
        state = self.ws.load(self.task)
        self.assertEqual(state['status'], 'awaiting_review')
        self.assertIsNone(state['review'])
        with self.assertRaises(ValueError):
            self.ws.finalize(self.task, self.ws.final_dir(self.task) / 'report-candidate.md')

    def test_full_result_not_summary_is_stored(self):
        source = self.ws.root / 'export.md'
        content = '## 摘要\n短摘要\n## 详细结果\n' + '完整结果' * 2000
        source.write_text(content, encoding='utf-8')
        self.flow.import_file(self.task, 'claude', source, True)
        self.assertEqual(self.ws.result_path(self.task, 'claude').read_text(encoding='utf-8'), content)
        self.assertLess((self.ws.task_dir(self.task) / 'summaries/claude.md').stat().st_size, 100)

    def test_empty_export_and_external_path_rejected(self):
        source = self.ws.root / 'empty.md'
        source.write_text(' ', encoding='utf-8')
        self.assertFalse(self.flow.import_file(self.task, 'chatgpt', source, True))
        with self.assertRaises(ValueError):
            self.flow.import_file(self.task, 'chatgpt', self.ws.root.parent / 'outside.md', True)

    def test_incomplete_fixture_not_imported(self):
        self.fill()
        self.flow.submit(self.task, 'chatgpt', local_auto=True)
        self.chrome.complete = False
        self.assertFalse(self.flow.collect_local(self.task, 'chatgpt'))

    def test_refresh_with_restored_input_still_requires_new_confirmation(self):
        self.fill()
        self.chrome.epoch = 2
        with self.assertRaises(ValueError):
            self.flow.submit(self.task, 'chatgpt', local_auto=True)
        self.assertFalse(any(n == 'click' for n, a in self.chrome.calls))

    def test_router_gate_is_checked_before_submission(self):
        class DenyingRouter:
            def require_lock(self, task_id, model):
                raise ValueError('锁定不一致')
        self.flow.router = DenyingRouter()
        self.fill()
        with self.assertRaises(ValueError):
            self.flow.submit(self.task, 'chatgpt', local_auto=True)
        self.assertFalse(any(n == 'click' for n, a in self.chrome.calls))

    def test_changed_result_invalidates_candidate_snapshot(self):
        for model in MODELS:
            source = self.ws.root / f'{model}.md'
            source.write_text('## 摘要\n原始结果\n## 详细结果\n完整内容', encoding='utf-8')
            self.flow.import_file(self.task, model, source, True)
        self.ws.result_path(self.task, 'claude').write_text('changed',encoding='utf-8')
        self.flow.load(self.task)
        metadata=json.loads((self.ws.final_dir(self.task)/'candidates.json').read_text(encoding='utf-8'))
        self.assertFalse(metadata['current'])


if __name__ == '__main__':
    unittest.main()
