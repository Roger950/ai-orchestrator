"""Live-MCP failure acceptance against the explicit local fixture only."""
from urllib.parse import urlsplit

from orchestrator.browser import pages_from_text, tool_text
from orchestrator.core import json_write
from orchestrator.router import ModelRouter


def verify_local_failures(ws, task_id, call_tool, fixture):
    uri = fixture.as_uri()
    pages = [p for p in pages_from_text(tool_text(call_tool('list_pages', {})), uri)
             if p['model'] == 'chatgpt' and urlsplit(p['url']).scheme == 'file']
    if len(pages) != 1:
        raise ValueError('需要唯一的本地 ChatGPT 测试页')
    page_id = pages[0]['pageId']
    cases = []
    try:
        for mode, expected in [('quota', 'quota_exhausted'), ('unavailable', 'unavailable'),
                               ('switch-failure', 'unknown')]:
            url = uri + '?model=chatgpt&mode=' + mode
            call_tool('navigate_page', {'pageId': page_id, 'type': 'url', 'url': url})
            calls = []

            def transport(name, arguments):
                calls.append(name)
                return call_tool(name, arguments)

            router = ModelRouter(ws.root, transport,
                                 ws.root / 'logs' / task_id / (mode + '-policy.json'), uri)
            page = {'pageId': page_id, 'url': url, 'model': 'chatgpt'}
            lock = router.switch(task_id, 'chatgpt', 'GPT-6', 'High', page)
            assert lock['lock_status'] == expected, lock
            if mode in {'quota', 'unavailable'}:
                assert 'click' not in calls, 'Permission/quota error must not click or retry'
            # Only this synthetic fixture is eligible for evaluating result emptiness.
            from orchestrator.browser import script_value
            empty = script_value(tool_text(call_tool('evaluate_script', {
                'pageId': page_id, 'function': "() => document.querySelector('#result').textContent === ''",
                'waitForStableDom': False})))
            assert empty is True, 'Failure test unexpectedly submitted a chat'
            cases.append({'case': mode, 'expected': expected, 'observed': lock['lock_status'],
                          'passed': True, 'chat_submitted': False, 'synthetic': True})
    finally:
        call_tool('navigate_page', {'pageId': page_id, 'type': 'url', 'url': uri+'?model=chatgpt'})
        json_write(ws.root / 'logs' / task_id / 'live-failure-acceptance.json', {'cases': cases, 'complete': len(cases)==3})
    return {'passed': len(cases), 'synthetic': True, 'chat_submitted': False}
