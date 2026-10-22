"""Real HTTP/SSE delivery boundary with controlled public model messages.

No model calls. Numbers on the page come from the tools, the reply is only
the checked final answer, and drafts stay in the audit.
"""
import datetime as dt
import json
from pathlib import Path
import tempfile
import threading
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer

import agent
import audit
import results
import server
import workspace
from profile import Profile

# Conversations persist per participant; never read or overwrite the real ones.
agent.SESSIONS_DIR = Path(tempfile.mkdtemp())
workspace.ROOT = Path(tempfile.mkdtemp())


def main():
    session = agent.Session('p16', Profile('p16'), clock=dt.date(2020, 3, 1))
    received = {}
    class Client:
        async def send(self, prompt, schema=None):
            received['queries'] = received.get('queries', 0) + 1
            reset = urllib.request.Request(base + '/api/reset', b'{"pid":"p16"}', {'Content-Type': 'application/json'})
            try:
                urllib.request.urlopen(reset, timeout=5)
                raise AssertionError('reset interrupted a running turn')
            except urllib.error.HTTPError as exc:
                assert exc.code == 409
            assert server.SESSIONS['p16'] is session
            # A draft is not a display source; only the checked final answer is.
            yield {'type': 'message', 'text': 'WRONG 999 hours', 'phase': None}
            args = {'days': 15}
            yield {'type': 'tool_call', 'id': 'read', 'name': 'read_record', 'arguments': args, 'success': True}
            value = await agent.call_tool(session, 'read_record', args)
            received['inspection'] = json.loads(value['content'][0]['text'])
            record_id = next(c['id'] for c in received['inspection']['evidence_cards'] if c['topic'] == 'recovery')
            received['answer'] = (f'The record has gaps, so it cannot yet tell whether things got worse [{record_id}]. '
                                  'Add more records and compare again.')
            yield {'type': 'message', 'phase': 'final_answer', 'text': json.dumps(
                {'answer': received['answer'], 'outcome': 'answer', 'reason': 'records'}, ensure_ascii=False)}
            yield {'type': 'completed', 'turn_id': 'test-turn', 'status': 'completed', 'error': None, 'duration_ms': 1}
    session._client = Client()
    previous_sessions, previous_log = server.SESSIONS, audit.LOG_DIR
    with tempfile.TemporaryDirectory() as temp:
        server.SESSIONS = {'p16': session}
        audit.LOG_DIR = Path(temp)
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{httpd.server_port}'
        try:
            body = {'pid': 'p16', 'as_of': '2020-03-01', 'turn': 'summary-test', 'message': 'review recovery'}
            req = urllib.request.Request(base + '/api/turn', json.dumps(body).encode(), {'Content-Type': 'application/json'})
            with urllib.request.urlopen(req) as response:
                assert json.load(response)['ok']
            events = []
            with urllib.request.urlopen(base + '/api/stream?turn=summary-test', timeout=30) as response:
                for line in response:
                    if line.startswith(b'data: '):
                        event = json.loads(line[6:])
                        events.append(event)
                        if event['kind'] == 'done':
                            break
            reply = next(e for e in events if e['kind'] == 'reply')
            assert reply['text'] == received['answer'] and reply['summary_status'] == 'accepted'
            assert received['queries'] == 1
            assert [(e['kind'], e['name']) for e in events if e['kind'] in ('tool', 'tool_result')] \
                == [('tool', 'read_record'), ('tool_result', 'read_record')]
            assert '999' not in json.dumps(events)
            coverage = next(s for s in reply['data_results'] if s['title'] == 'Record coverage')
            assert coverage['rows'][2][:2] == ['Recovery', '7 of 15']
            # Only the cited domain gets its own table: recovery, not sleep or injury.
            tables = [s['title'] for s in reply['data_results']]
            assert 'Recovery records' in tables and 'Sleep records' not in tables and 'Injury records' not in tables
            row = audit.read('p16')[-1]
            assert row['output'] == received['answer'] and row['data_results'] == reply['data_results']
            assert 'WRONG 999' in json.dumps(row['execution'])
            print('PASS reset is rejected while the same participant has a running turn')
            print('PASS HTTP/SSE shows exact tool values in tables and the checked final answer as the reply')
            print('PASS model drafts are audit-only, not streamed to the UI')
        finally:
            httpd.shutdown()
            httpd.server_close()
            server.SESSIONS, audit.LOG_DIR = previous_sessions, previous_log

    walk = {'date': '2020-03-06', 'activity': 'walking', 'minutes': 10, 'intensity': 'easy'}
    rest = {'date': '2020-03-07', 'activity': 'rest', 'minutes': 0, 'intensity': 'rest'}
    created = {'draft': 'created', 'draft_diff': workspace.diff(None, [walk])}
    plan = next(s for s in results.build_sections([], created) if s['title'] == 'Plan draft')
    assert plan['columns'][-1] == 'Intensity' and plan['rows'] == [['Fri 6 Mar', 'Walking', 10, 'easy']]
    revised = {'draft': 'revised', 'draft_diff': workspace.diff([walk, rest], [{**walk, 'minutes': 20}])}
    plan = next(s for s in results.build_sections([], revised) if s['title'] == 'Plan draft')
    assert plan['rows'] == [['Fri 6 Mar', 'Walking', 20, 'easy', 'changed'],
                            ['Sat 7 Mar', 'Rest', 0, 'rest', 'removed']]
    for unchanged in (None, {'draft': 'unchanged', 'draft_diff': []}, {'draft': 'removed', 'draft_diff': []}):
        assert not any(s['title'] == 'Plan draft' for s in results.build_sections([], unchanged))
    print('PASS the plan draft table comes from the workspace change and marks what changed; '
          'no change means no displayed schedule')


if __name__ == '__main__':
    main()
