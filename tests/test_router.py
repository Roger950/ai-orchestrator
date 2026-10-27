import json
import tempfile
import unittest
from pathlib import Path

from orchestrator.core import Workspace, MODELS
from orchestrator.browser import BrowserWorkflow
from orchestrator.router import ModelRouter, selector_evidence, canonical, profile


class SelectorChrome:
    def __init__(self):
        self.current = 'GPT-6'
        self.level = 'Medium'
        self.choices = ['GPT-6', 'GPT-7 Preview']
        self.quota = False
        self.upgrade = False
        self.fail_switch = False
        self.composer = True
        self.calls = []
        self.count = 0
        self.fault = False

    def __call__(self, name, args):
        self.calls.append((name, args))
        if self.fault:
            raise RuntimeError('MCP disconnected')
        if name == 'click':
            if args['uid'] == 'high' and not self.fail_switch:
                self.level = 'High'
            elif args['uid'].startswith('option-') and not self.fail_switch:
                self.current = self.choices[int(args['uid'].split('-')[1])]
            return ''
        if name == 'take_snapshot':
            lines = [f'uid=selector button "Model selector, current model is {self.current}" haspopup="menu" expanded',
                     f'uid=high radio "High" checked={str(self.level=="High").lower()}',
                     f'uid=medium radio "Medium" checked={str(self.level=="Medium").lower()}']
            for i, model in enumerate(self.choices):
                lines.append(f'uid=option-{i} menuitemradio "{model}" checked={str(model==self.current).lower()}')
            if self.composer:
                lines.append('uid=input textbox "Message"')
            if self.quota:
                lines.append('uid=quota alert "Quota exhausted"')
            if self.upgrade:
                lines.append('uid=upgrade dialog "Upgrade required"')
            return '\n'.join(lines)
        raise AssertionError(name)


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='router-')
        self.addCleanup(self.tmp.cleanup)
        self.ws = Workspace(self.tmp.name)
        self.task = self.ws.create('复杂医学文献分析', 'Router test', 'router-test')
        self.chrome = SelectorChrome()
        self.fixture = (self.ws.root / 'fixture.html').as_uri()
        self.path = self.ws.root / 'logs/fixture-policy.json'
        self.router = ModelRouter(self.ws.root, self.chrome, self.path, self.fixture)
        self.page = {'pageId': 1, 'url': self.fixture+'?model=chatgpt', 'model': 'chatgpt'}

    def observe(self):
        return self.router.observe('chatgpt', self.page)

    def model(self, name='GPT-6', platform='chatgpt'):
        return next(m for m in self.router.load()['platforms'][platform]['models'] if m['name']==name)

    def test_initial_candidates_are_not_permissions(self):
        self.assertEqual(self.model()['state'], 'unknown')
        self.assertIsNone(self.model()['exists'])
        self.assertEqual(self.router.rank('chatgpt')['ranked'], [])

    def test_new_version_discovered_but_not_enabled(self):
        self.observe()
        model = self.model('GPT-7 Preview')
        self.assertTrue(model['exists'])
        self.assertEqual(model['state'], 'discovered')
        self.assertFalse(model['enabled'])

    def test_selected_and_composer_verified_not_merely_menu_item(self):
        self.observe()
        self.assertEqual(self.model()['state'], 'available')
        self.chrome.composer = False
        self.observe()
        self.assertEqual(self.model()['state'], 'discovered')
        self.assertFalse(self.model()['enabled'])

    def test_medical_prefers_high_structured_medium(self):
        self.observe()
        self.assertEqual(self.router.rank('chatgpt', 'medical')['ranked'][0]['reasoning_level'], 'High')
        self.assertEqual(self.router.rank('chatgpt', 'structured')['ranked'][0]['reasoning_level'], 'Medium')

    def test_actual_switch_and_lock_verification(self):
        lock = self.router.switch(self.task, 'chatgpt', 'GPT-6', 'High', self.page)
        self.assertEqual(lock['lock_status'], 'locked')
        self.assertEqual(lock['actual_confirmed_model'], 'GPT-6')
        self.assertEqual(lock['reasoning_level'], 'High')
        self.assertTrue(lock['verified_at'])

    def test_switch_failure_is_unknown(self):
        self.chrome.fail_switch = True
        lock = self.router.switch(self.task, 'chatgpt', 'GPT-6', 'High', self.page)
        self.assertEqual(lock['lock_status'], 'unknown')
        with self.assertRaises(ValueError):
            self.router.require_lock(self.task, 'chatgpt')

    def test_lock_mismatch_stops_execution(self):
        self.router.lock(self.task, 'chatgpt', 'GPT-6', 'Medium', self.page)
        self.chrome.current = 'GPT-7 Preview'
        with self.assertRaises(ValueError):
            self.router.require_lock(self.task, 'chatgpt')
        lock = self.router.load()['locks'][self.task]['chatgpt']
        self.assertEqual(lock['lock_status'], 'unknown')

    def test_quota_does_not_fallback(self):
        self.router.lock(self.task, 'chatgpt', 'GPT-6', 'Medium', self.page)
        self.chrome.quota = True
        self.observe()
        self.assertEqual(self.model()['state'], 'quota_exhausted')
        self.assertEqual(self.router.rank('chatgpt')['status'], 'quota_exhausted')
        self.assertFalse(any(n=='click' for n,a in self.chrome.calls))
        self.chrome.quota = False
        self.observe()
        self.assertEqual(self.router.rank('chatgpt')['status'], 'quota_exhausted')

    def test_upgrade_is_unavailable_and_no_click_or_retry(self):
        self.chrome.upgrade = True
        for _ in range(2):
            lock=self.router.switch(self.task, 'chatgpt', 'GPT-6', 'High', self.page)
            self.assertEqual(lock['lock_status'], 'unavailable')
        self.assertFalse(any(n=='click' for n,a in self.chrome.calls))

    def test_claude_free_whitelist_blocks_opus_even_selected(self):
        self.chrome.choices=['Claude Sonnet 5.5','Claude Opus 5.5']
        self.chrome.current='Claude Opus 5.5'
        page={'pageId':3,'url':self.fixture+'?model=claude','model':'claude'}
        lock=self.router.switch(self.task,'claude','Claude Opus 5.5','High',page)
        self.assertEqual(lock['lock_status'],'unavailable')
        self.assertEqual(self.model('Claude Opus 5.5','claude')['state'],'unavailable')
        self.assertFalse(any(n=='click' for n,a in self.chrome.calls))
        with self.assertRaises(ValueError):
            self.router.confirm_switch(self.task,'claude','Claude Opus 5.5','High','用户')

    def test_real_switch_requires_platform_authorization(self):
        real = ModelRouter(self.ws.root,self.chrome)
        page={'pageId':2,'url':'https://chatgpt.com/','model':'chatgpt'}
        lock=real.switch(self.task,'chatgpt','GPT-6','High',page)
        self.assertIn('manual_instruction',lock)
        self.assertFalse(any(n=='click' for n,a in self.chrome.calls))

    def test_fixture_cannot_contaminate_real_policy(self):
        with self.assertRaises(ValueError):
            ModelRouter(self.ws.root,self.chrome,fixture_url=self.fixture)

    def test_new_model_cannot_replace_lock_without_confirmation_even_repeated(self):
        self.router.lock(self.task,'chatgpt','GPT-6','Medium',self.page)
        self.chrome.current='GPT-7 Preview'
        for _ in range(2):
            lock=self.router.lock(self.task,'chatgpt','GPT-7 Preview','Medium',self.page)
            self.assertEqual(lock['lock_status'],'unknown')
        self.router.confirm_switch(self.task,'chatgpt','GPT-7 Preview','Medium','用户')
        lock=self.router.lock(self.task,'chatgpt','GPT-7 Preview','Medium',self.page)
        self.assertEqual(lock['lock_status'],'locked')

    def test_expired_permission_not_ranked(self):
        self.observe()
        data=self.router.load()
        data['platforms']['chatgpt']['models'][0]['verified_at']='2000-01-01T00:00:00+00:00'
        self.router.save(data)
        self.assertEqual(self.router.rank('chatgpt')['ranked'],[])

    def test_unselected_option_and_chat_text_not_current_evidence(self):
        evidence=selector_evidence('uid=1 StaticText "GPT-6 is selected"\nuid=2 menuitemradio "GPT-6" checked=false\nuid=3 textbox "Message"')
        self.assertIsNone(evidence['actual'])

    def test_live_selector_label_formats(self):
        self.assertEqual(canonical('模型：Sonnet 5.5 中等'),'Claude Sonnet 5.5')
        self.assertEqual(canonical('Sonnet 5.5 处理简单任务效率最高'),'Claude Sonnet 5.5')
        self.assertEqual(canonical('Selected 4 Argon Complex tasks'),'Gemini 4 Argon')
        evidence=selector_evidence('uid=a button "模型：Sonnet 5.5 中等" haspopup="menu"\nuid=b menuitemradio "Sonnet 5.5 处理简单任务效率最高" checked\nuid=c textbox "Message"')
        self.assertEqual(evidence['actual'],'Claude Sonnet 5.5')
        self.assertEqual(evidence['reasoning_level'],'Medium')

    def test_routing_plan_is_saved_without_approving_report(self):
        self.observe()
        plan=self.router.plan(self.task,'复杂医学文献分析')
        self.assertEqual(plan['task_type'],'medical')
        self.assertTrue((self.ws.task_dir(self.task)/'routing-plan.json').exists())
        self.assertIsNone(self.ws.load(self.task)['review'])

    def test_multiple_verified_models_rank_by_task_after_permission_filter(self):
        self.observe()
        self.chrome.current = 'GPT-7 Preview'
        self.observe()
        data = self.router.load()
        new = next(m for m in data['platforms']['chatgpt']['models'] if m['name']=='GPT-7 Preview')
        new['capabilities'].update(speed=5, quota_economy=5, medicine=1, reasoning=1, quality=1)
        self.router.save(data)
        self.assertEqual(len(self.router.rank('chatgpt')['ranked']),2)
        self.assertEqual(self.router.rank('chatgpt','medical')['ranked'][0]['model'],'GPT-6')
        self.assertEqual(self.router.rank('chatgpt','structured')['ranked'][0]['model'],'GPT-7 Preview')

    def test_tool_disconnect_revokes_lock_and_availability(self):
        self.router.lock(self.task,'chatgpt','GPT-6','Medium',self.page)
        self.chrome.fault=True
        with self.assertRaises(RuntimeError):
            self.router.require_lock(self.task,'chatgpt')
        self.assertEqual(self.router.load()['locks'][self.task]['chatgpt']['lock_status'],'unknown')
        self.assertFalse(self.model()['enabled'])

    def test_locked_reasoning_change_requires_confirmation_before_click(self):
        self.router.lock(self.task,'chatgpt','GPT-6','Medium',self.page)
        with self.assertRaises(ValueError):
            self.router.switch(self.task,'chatgpt','GPT-6','High',self.page)
        self.assertFalse(any(n=='click' for n,a in self.chrome.calls))

    def test_missing_reasoning_level_cannot_lock_default_by_assumption(self):
        self.chrome.level='not visible'
        self.observe()
        lock=self.router.lock(self.task,'chatgpt','GPT-6','Default',self.page)
        self.assertEqual(lock['lock_status'],'unknown')
        lock=self.router.lock(self.task,'chatgpt','GPT-6',None,self.page)
        self.assertEqual(lock['lock_status'],'unknown')

    def test_claude_unseen_opus_is_policy_denied_not_missing_product(self):
        self.chrome.current='Claude Sonnet 5.5'
        self.chrome.choices=['Claude Sonnet 5.5']
        page={'pageId':3,'url':self.fixture+'?model=claude','model':'claude'}
        self.router.observe('claude',page)
        opus=self.model('Claude Opus 5.5','claude')
        self.assertEqual(opus['state'],'unavailable')
        self.assertEqual(opus['account_access'],'policy_denied')
        self.assertIsNone(opus['exists'])

    def test_connection_loss_disables_routing_but_preserves_observed_history(self):
        self.router.lock(self.task,'chatgpt','GPT-6','Medium',self.page)
        self.router.connection_lost('Chrome stopped')
        data=self.router.load()
        self.assertEqual(data['connection']['status'],'unavailable')
        self.assertEqual(data['locks'][self.task]['chatgpt']['lock_status'],'unknown')
        self.assertEqual(data['locks'][self.task]['chatgpt']['actual_confirmed_model'],'GPT-6')
        self.assertEqual(self.router.rank('chatgpt')['ranked'],[])
        self.assertTrue(self.model()['exists'])


if __name__=='__main__':
    unittest.main()
