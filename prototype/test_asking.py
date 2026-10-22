"""Asking before planning, over HTTP/SSE, with an explicitly controlled coach SDK.

The coach asks in its own words: no tool gates the plan and no code writes
the questions. What code still guarantees is checked here: a turn that asks
waits for the person and leaves the plan draft as it was, the draft cannot
change with a question or a monitoring note, and a warning symptom is a
safety stop.
"""
import datetime as dt
import json
from pathlib import Path
import tempfile
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import anyio

import agent
import audit
import server
import workspace
from profile import Profile

# Conversations and workspaces persist per participant; never touch the real ones.
agent.SESSIONS_DIR = Path(tempfile.mkdtemp())
workspace.ROOT = Path(tempfile.mkdtemp())

DAY = '2020-03-01'
PLAN = {'days': [{'date': '2020-03-02', 'activity': 'walking', 'minutes': 20, 'intensity': 'easy'},
                 {'date': '2020-03-03', 'activity': 'rest', 'minutes': 0, 'intensity': 'rest'}]}


def final(answer, outcome='answer', reason='r'):
    return json.dumps({'answer': answer, 'outcome': outcome, 'reason': reason}, ensure_ascii=False)


def write_plan(folder):
    (Path(folder) / workspace.DRAFT).write_text(json.dumps(PLAN))


def main():
    s = agent.Session('p16', Profile('p16'), clock=dt.date.fromisoformat(DAY))
    assert 'check_personal_context' not in s.tools()
    assert 'current_state' in agent.system_prompt(s) and 'check_personal_context' not in agent.system_prompt(s)
    print('PASS no context tool or gate: the catalogue and the instructions ask the coach to find out, in its own words')

    queries = []

    question = ('Which days can you train next week, and for how long each time? '
                'How have you been feeling lately - any illness or poor sleep?')

    class Client:
        workspace = str(Path(tempfile.mkdtemp()) / 'workspace')

        async def send(self, prompt, schema=None):
            queries.append(prompt)
            tools = s.tools()
            if 'training for next week' in prompt:
                write_plan(self.workspace)      # a plan riding on the question
                text = final(question, 'clarification')
            elif 'returned for correction' in prompt:
                text = final(question, 'clarification')
            else:
                read = await tools['read_record'].handler({'days': 28})
                card = next(c for c in json.loads(read['content'][0]['text'])['evidence_cards']
                            if c['kind'] == 'record')
                write_plan(self.workspace)
                text = final(f'Start from the walking habit in your record and keep this week easy [{card["id"]}].')
            yield {'type': 'message', 'text': 'UNCITED draft: run 99 minutes', 'phase': None}
            yield {'type': 'message', 'text': text, 'phase': 'final_answer'}
            yield {'type': 'completed', 'turn_id': 't', 'status': 'completed', 'error': None, 'duration_ms': 1}
    s._client = Client()
    old = server.SESSIONS, audit.LOG_DIR
    with tempfile.TemporaryDirectory() as tmp:
        server.SESSIONS, audit.LOG_DIR = {'p16': s}, Path(tmp)
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{httpd.server_port}'
        try:
            replies, states = [], []
            for index, message in enumerate(['Help me schedule my training for next week.',
                                                   "Monday, Wednesday and Friday evenings. I've been worn out "
                                                   'from overtime lately, no pain.']):
                turn_id = f'ask-{index}'
                req = urllib.request.Request(base + '/api/turn', json.dumps(
                    {'pid': 'p16', 'as_of': DAY, 'turn': turn_id, 'message': message}).encode(),
                    {'Content-Type': 'application/json'})
                with urllib.request.urlopen(req) as r:
                    assert json.load(r)['ok']
                events = []
                with urllib.request.urlopen(base + '/api/stream?turn=' + turn_id, timeout=30) as r:
                    for line in r:
                        if line.startswith(b'data: '):
                            events.append(json.loads(line[6:]))
                            if events[-1]['kind'] == 'done':
                                break
                assert 'UNCITED' not in json.dumps(events)
                replies.append(next(e for e in events if e['kind'] == 'reply'))
                states.append([e for e in events if e['kind'] == 'state'][-1]['state'])
            assert replies[0]['stop'] == 'awaiting_user' and replies[0]['workspace']['draft'] == 'unchanged'
            assert 'may change only in an answer' in queries[1]
            assert 'How have you been feeling lately' in replies[0]['text'] and states[0] == 'awaiting_user'
            assert replies[1]['stop'] == 'answered' and replies[1]['workspace']['draft'] == 'created'
            assert any(t['title'] == 'Plan draft' for t in replies[1]['data_results'])
            with urllib.request.urlopen(base + '/api/plan?pid=p16&as_of=' + DAY) as r:
                assert json.load(r)['draft'] == PLAN
            assert states[1] == 'complete'
            assert [row['stop'] for row in audit.read('p16')] == ['awaiting_user', 'answered']
            print('PASS HTTP/SSE: a turn that asks waits for the person and a draft written with the '
                  'question is undone; the answer after it writes the draft the page shows')
            for historical_day in ('2020-02-20', '2020-03-20'):
                with urllib.request.urlopen(base + '/api/memory?pid=p16&as_of=' + historical_day) as r:
                    assert r.status == 200
            assert str(s.clock) == DAY
            print('PASS reading historical memory does not mutate the active conversation clock')
        finally:
            httpd.shutdown()
            httpd.server_close()
            server.SESSIONS, audit.LOG_DIR = old

    s.evidence_cards = {}                    # a new turn, before any record read
    workspace.stage(s.workspace_home, s.workspace_stage, s.knowledge_day)
    (s.workspace_stage / workspace.DRAFT).write_text(json.dumps({'days': PLAN['days'][:1]}))
    _, refused = agent._check_final(s, 'monitor', final('The record shows short sleep.'))
    assert len(refused) == 1 and 'may change only in an answer' in refused[0]
    assert json.loads((s.workspace_stage / workspace.DRAFT).read_text()) == PLAN
    print('PASS a monitoring note cannot change the plan draft')

    async def symptom():
        class Client:
            async def send(self, prompt, schema=None):
                yield {'type': 'message', 'phase': 'final_answer', 'text': final(
                    'Stop exercising now and seek medical care; call local emergency services if it is severe.',
                    'safety_advice', reason='chest pain during exercise')}
                yield {'type': 'completed', 'status': 'completed', 'error': None, 'duration_ms': 1}
        s._client = Client()
        previous, audit.LOG_DIR = audit.LOG_DIR, Path(tempfile.mkdtemp())
        try:
            out = await agent.turn(s, 'I had chest pain just now while running')
        finally:
            audit.LOG_DIR = previous
        assert out['stop'] == 'safety_advice' and out['workspace']['draft'] == 'unchanged'
        assert 'safety_advice: chest pain during exercise' in out['trace']
    anyio.run(symptom)
    print('PASS a warning symptom ends the turn as a recorded safety stop, with no plan and no lock')


if __name__ == '__main__':
    main()
