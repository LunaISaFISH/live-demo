"""Citation-checked final answers, through actual HTTP/SSE with a controlled model."""
import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import agent
import audit
import evidence
import server
import workspace
from profile import Profile

# Conversations persist per participant; never read or overwrite the real ones.
agent.SESSIONS_DIR = Path(tempfile.mkdtemp())
workspace.ROOT = Path(tempfile.mkdtemp())


def final(answer, outcome='answer', reason='records'):
    return {'type': 'message', 'phase': 'final_answer',
            'text': json.dumps({'answer': answer, 'outcome': outcome, 'reason': reason}, ensure_ascii=False)}


DONE = {'type': 'completed', 'turn_id': 'test-turn', 'status': 'completed', 'error': None, 'duration_ms': 1}


def main():
    s = agent.Session('p16', Profile('p16'), clock=dt.date(2020, 3, 1))
    saved = {'sends': 0}

    class Client:
        async def send(self, prompt, schema=None):
            saved['sends'] += 1
            assert 'context evidence cards' in prompt or 'returned for correction' in prompt
            assert schema == evidence.SCHEMA
            if saved['sends'] == 1:
                out = await agent.call_tool(s, 'read_record', {'days': 15})
                data = json.loads(out['content'][0]['text'])
                record = s.evidence_cards[data['domains']['recovery']['card_id']]
                assert record['evidence']['this_window']['days'] == 7
                # The model reads the data once: the card list holds IDs, not copies.
                assert all('evidence' not in c for c in data['evidence_cards'])
                saved['record'] = record['id']
                yield {'type': 'message', 'phase': None, 'text': 'UNCITED 999 causal draft'}
                # An invented citation: goes back to this session, not to the page.
                yield final('Your recovery record is sparse [deadbeef-e7].')
            else:
                saved['answer'] = (f'Your recovery record is sparse [{saved["record"]}], so it cannot yet '
                                   'tell whether you are back to normal.')
                yield final(saved['answer'])
            yield DONE

    s._client = Client()
    old_sessions, old_log = server.SESSIONS, audit.LOG_DIR
    with tempfile.TemporaryDirectory() as tmp:
        server.SESSIONS, audit.LOG_DIR = {'p16': s}, Path(tmp)
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{httpd.server_port}'
        try:
            body = {'pid': 'p16', 'as_of': '2020-03-01', 'turn': 'evidence-test', 'message': 'Am I back to normal lately?'}
            req = urllib.request.Request(base+'/api/turn', json.dumps(body).encode(), {'Content-Type': 'application/json'})
            with urllib.request.urlopen(req) as r:
                assert json.load(r)['ok']
            events = []
            with urllib.request.urlopen(base+'/api/stream?turn=evidence-test', timeout=30) as r:
                for line in r:
                    if line.startswith(b'data: '):
                        events.append(json.loads(line[6:]))
                        if events[-1]['kind'] == 'done':
                            break
            reply = next(e for e in events if e['kind'] == 'reply')
            assert reply['summary_status'] == 'accepted' and reply['text'] == saved['answer']
            assert reply['stop'] == 'answered' and saved['sends'] == 2
            assert '999' not in json.dumps(events)
            graph = audit.read('p16')[-1]['execution']
            assert 'deadbeef-e7' in graph['corrections'][0]['errors'][0]
            assert graph['final_answer']['answer'] == saved['answer']
            assert 'UNCITED 999' in json.dumps(graph)
            sources = next(x for x in reply['data_results'] if x['title'] == 'Evidence sources')
            assert sources['refs'] == [saved['record']] and sources['rows'][0][0] == saved['record'].split('-')[1]
            assert any(x['title'] == 'Record coverage' for x in reply['data_results'])
            print('PASS an invented citation returns to the same session and the corrected answer is shown')
            print('PASS raw drafts stay audit-only; the page shows only the checked final answer')
            print('PASS cited sources are listed in citation order, linkable by their full ID')
        finally:
            httpd.shutdown()
            httpd.server_close()
            server.SESSIONS, audit.LOG_DIR = old_sessions, old_log

    cards = s.evidence_cards
    record = saved['record']
    knowledge = next(c['id'] for c in cards.values() if c['kind'] in evidence.NON_PERSONAL)
    ok = {'answer': f'Sparse records [{record}].', 'outcome': 'answer', 'reason': 'records'}
    assert evidence.validate(ok, cards) == []
    assert evidence.cited(f'a [{record}] b [{record}] [x]') == [record]
    for bad, expected in (
            ({**ok, 'answer': 'Sparse records [0000abcd-e1].'}, 'not evidence from this turn'),
            ({**ok, 'answer': f'Sparse records [{record.split("-")[0][::-1]}-e1].'}, 'not evidence from this turn'),
            ({**ok, 'answer': f'Guidance only [{knowledge}].'}, 'cited none of them'),
            ({**ok, 'answer': 'No citation at all.'}, 'cited none of them'),
            ({**ok, 'outcome': 'diagnosis'}, 'outcome must be one of'),
            ({**ok, 'outcome': 'safety_advice', 'reason': ' '}, 'needs its reason'),
            ({**ok, 'plan': None}, 'exactly answer, outcome and reason'),
            ({**ok, 'answer': '  '}, 'empty')):
        errors = evidence.validate(bad, cards)
        assert any(expected in e for e in errors), (bad, errors)
    print('PASS invented, stale and guidance-only citations, unknown outcomes, missing reasons and extra fields are returned')

    no_records = {k: v for k, v in cards.items() if v['kind'] not in ('record', 'computed')}
    assert evidence.validate({'answer': 'Today is Tuesday.', 'outcome': 'answer', 'reason': 'clock'},
                             no_records) == []
    print('PASS an answer that reads no records needs no citation')

    # Counterfactual record deletion changes tool evidence, not the test prompt.
    import observe
    full = Profile('p16').snapshot(dt.date(2020, 3, 1))
    sparse = copy.copy(full)
    sparse.reports = []

    def cards_for(p):
        found = {}
        evidence.add_cards(found, 'read_record', observe.read_record(p, 15), 'test')
        return next(c for c in found.values() if c['topic'] == 'recovery')['evidence']['this_window']
    before, after = cards_for(full), cards_for(sparse)
    assert before['days'] == 7 and 'readiness' in before
    assert after['days_with_records'] == 0 and 'readiness' not in after and 'fatigue' not in after
    print('PASS removing records leaves no recovery answers to read, rather than normal recovery')


if __name__ == '__main__':
    main()
