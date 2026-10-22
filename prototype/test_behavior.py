"""Behavior evidence regressions over the real HTTP and tool boundaries.

Run: .venv/bin/python prototype/test_behavior.py
No model calls. Live behavior cases live in behavior_eval.py.
"""
import datetime as dt
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import server


def main():
    failures = []
    def check(name, condition):
        print(('PASS ' if condition else 'FAIL ') + name)
        if not condition:
            failures.append(name)

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{httpd.server_port}'
    def inspect(domain, start, end, extra=''):
        with urllib.request.urlopen(
            f'{base}/api/inspect?pid=p16&as_of=2020-03-01&domain={domain}'
            f'&start={start}&end={end}{extra}') as response:
            return json.load(response)
    try:
        recovery = inspect('recovery', '2020-02-16', '2020-03-01')['observations'][0]
        dq = recovery['data_quality']
        check('15 calendar days are distinct from 7 report records',
              dq.get('calendar_days') == 15 and dq.get('record_count') == 7
              and dq.get('recorded_days') == 7)
        check('no baseline claim without a comparison', recovery['status'] == 'observed')
        check('descriptive data does not certify explanatory sufficiency',
              dq['sufficiency'] == 'not_assessed')
        training = inspect('training', '2020-01-06', '2020-02-03')['observations'][0]
        minutes = next(e for e in training['evidence'] if e['name'] == 'median_minutes')
        check('device-only duration is attributed to device', minutes['source'] == 'device')
        check('event density is not wearable or self-report completeness',
              training['data_quality'].get('coverage_basis') == 'days_with_recorded_events / calendar_days')
        empty = inspect('training', '2020-03-01', '2020-03-01')['observations'][0]
        check('no recorded sessions does not establish no exercise',
              empty['status'] == 'insufficient' and any('unrecorded' in x for x in empty['limitations']))
        compared = inspect('recovery', '2020-02-16', '2020-03-01',
                           '&compare_to_start=2020-02-01&compare_to_end=2020-02-15')['observations'][1]
        check('comparison preserves reference sample and calendar counts',
              compared.get('reference_data_quality', {}).get('calendar_days') == 15
              and 'record_count' in compared.get('reference_data_quality', {}))
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()
    from behavior_eval import sparse_answer_checks
    check('numeric evaluator accepts label-first and number-first answers',
          all(sparse_answer_checks('Calendar days: 15; reports: 7; coverage 47%').values())
          and all(sparse_answer_checks('calendar days = 15; record_count = 7; coverage 47%').values())
          and all(sparse_answer_checks('15 calendar days; 7 reports; coverage 46.7%').values())
          and all(sparse_answer_checks('15 calendar days, 7 records, 47% coverage').values()))
    check('numeric evaluator rejects swapped counts and contradictory coverage',
          not all(sparse_answer_checks('7 calendar days; 15 reports; coverage 47%').values())
          and not sparse_answer_checks('15 calendar days, 7 reports, 47% coverage, or 49% coverage')['stated_coverage_consistent'])
    check_execution(check)
    assert not failures, f'{len(failures)} failed: {failures}'


def check_execution(check):
    import tempfile
    from pathlib import Path
    import anyio
    import agent
    import audit
    import plan
    from profile import Profile
    import workspace
    agent.SESSIONS_DIR = Path(tempfile.mkdtemp())   # never the real conversations
    workspace.ROOT = Path(tempfile.mkdtemp())

    old = audit.LOG_DIR
    with tempfile.TemporaryDirectory() as directory:
        audit.LOG_DIR = Path(directory)
        session = agent.Session('p16', Profile('p16'), clock=dt.date(2020, 3, 1))
        tools = {tool.name: tool for tool in agent.build_tools(session)}
        expected = {}
        class Client:
            fail = False
            omit_result = False
            async def send(self, prompt, schema=None):
                expected.setdefault('prompt', prompt)
                args = {'days': 15, 'end': '2020-03-01'}
                yield {'type': 'message', 'text': 'PLAN_BEFORE_MEMORY', 'phase': None}
                result = await agent.call_tool(session, 'read_record', args)
                expected.setdefault('result', result)
                yield {'type': 'tool_call', 'id': 'call-1', 'name': 'read_record',
                       'arguments': args, 'success': True}
                card = next(c['id'] for c in json.loads(result['content'][0]['text'])['evidence_cards']
                            if c['topic'] == 'recovery')
                expected['answer'] = f'Your recovery records are sparse [{card}].'
                yield {'type': 'message', 'phase': 'final_answer', 'text': json.dumps(
                    {'answer': expected['answer'], 'outcome': 'answer', 'reason': 'records'})}
                if self.omit_result:
                    return
                yield {'type': 'completed', 'turn_id': 'test-turn',
                       'status': 'failed' if self.fail else 'completed',
                       'error': {'message': 'usage limit'} if self.fail else None, 'duration_ms': 1}
        client = Client()
        session._client = client
        async def run():
            invalid = await tools['inspect_sensor_window'].handler({'start': 'bad', 'end': 'bad'})
            check('tool failures carry the error flag', invalid.get('is_error') is True)
            out = await agent.turn(session, 'inspect my recovery')
            record = audit.read('p16')[-1]['execution']
            inspected = next(x for x in record['tool_executions'] if x['name'] == 'read_record')
            check('exact tool parameters and untruncated results survive audit',
                  inspected['result'] == expected['result'] and inspected['input'] == {'days': 15, 'end': '2020-03-01'})
            check('public call IDs, exact prompt and terminal metadata survive audit',
                  record['messages'][1]['id'] == 'call-1'
                  and record['query'] == expected['prompt']
                  and record['result']['turn_id'] == 'test-turn')
            check('model drafts stay in the audit; only the checked final answer is the reply',
                  out['reply'] == expected['answer'] and out['summary_status'] == 'accepted'
                  and 'PLAN_BEFORE_MEMORY' in json.dumps(record))
            client.fail = True
            try:
                await agent.turn(session, 'fail this run')
            except RuntimeError:
                pass
            failed = audit.read('p16')[-1]
            check('failed terminal state is audited and cleared',
                  failed['execution']['status'] == 'failed'
                  and failed['execution']['result']['status'] == 'failed'
                  and session._execution is None and session._on_event is None)
            client.fail, client.omit_result = False, True
            try:
                await agent.turn(session, 'truncated stream')
            except RuntimeError:
                pass
            check('a truncated model stream cannot be recorded as success',
                  audit.read('p16')[-1]['execution']['status'] == 'failed')
        try:
            anyio.run(run)
            odd = {'days': [{'date': '2020-03-02', 'activity': 'yoga', 'minutes': 20, 'intensity': 'max'}]}
            problems = ' '.join(workspace.plan_problems(odd, dt.date(2020, 3, 1)))
            check('a plan draft is held to the activity and intensity vocabulary the page shows',
                  ', '.join(plan.ACTIVITY_NAMES) in problems and ', '.join(plan.INTENSITY) in problems)
            context = plan.planning_context(session.visible())
            check('planning context retains load units and readiness sample window',
                  context['training_load'].get('units', {}).get('chronic_28d_per_week') == 'sRPE/week'
                  and context['readiness_trend'].get('calendar_days') == 14
                  and context['readiness_trend'].get('record_count') == 6)
            check('uncomputed readiness trend is not called flat',
                  context['readiness_trend']['direction'] == 'not_assessed')
            check('planning context does not certify normal recovery by default',
                  'your recovery markers are all within your normal range'
                  not in context['capacity_today']['reasons'])
            schema = tools['read_record'].input_schema
            check('read_record takes only an optional window length and end, each described',
                  set(schema['properties']) == {'days', 'end'} and not schema.get('required')
                  and all('description' in v for v in schema['properties'].values()))
        finally:
            audit.LOG_DIR = old


if __name__ == '__main__':
    main()
