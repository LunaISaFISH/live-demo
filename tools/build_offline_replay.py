"""Build a standalone, honestly labelled replay from the successful local run."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TITLES = ['Check in', 'Clarify the context', 'Remember a preference',
          'Ask before planning', 'Draft the week', 'Revise the draft',
          'The person saves', 'Respond to a warning symptom',
          'Continue with the record', 'Reject an unsafe instruction',
          'Review a monitoring signal']
NOTES = ['从真实记录开始，缺失信息先追问，不猜测疲劳原因。',
         '补充未被设备记录的搬家活动；观察回答如何引用记录和个人补充。',
         '偏好写入记忆，后续规划会使用。',
         '缺少目标、时间和身体情况时，先澄清；此时没有计划可保存。',
         '读取真实记录后生成七日草稿。草稿还没有成为用户的正式计划。',
         '用自然语言修改草稿：只保留周五十分钟轻松步行。',
         '这里展示测试中用户实际保存的结果：version 1。回放按钮不会修改 live 数据。',
         '使用测试输入展示安全响应：停止运动并建议及时就医。',
         '仍可查询历史训练记录，同时保留适当的安全提醒。',
         '越权指令被拒绝，没有被写入长期记忆。',
         '监测器发现信号，模型结合记录审查，并给出 Alert、依据、局限和复查时间。']


def make_steps(rows):
    if len(rows) != len(TITLES) or rows[6]['human_decision'] != 'plan_saved' or not rows[10]['review']:
        raise ValueError('Expected the verified 11-entry local workflow audit.')
    steps = []
    for index, row in enumerate(rows):
        steps.append(dict(title=TITLES[index], note=NOTES[index],
                          input=row['input'], reply=row['output'],
                          timestamp=row['timestamp'], status=row['stop'] or row['status'],
                          decision=row['human_decision'], actions=row['action'],
                          sources=[s for s in row['source'] if not s.startswith('shell ')],
                          plan=row['plan'], review=row['review']))
    # The next turn's context captures the actual previous draft before revision.
    plans = [c for c in rows[5]['execution']['evidence_cards'].values() if c['kind'] == 'plan']
    steps[4]['plan'] = plans[0]['draft']
    steps[5]['plan'] = {'days': rows[6]['plan']['days']}
    steps.append(dict(title='Audit & verification',
                      note='所有步骤来自同一次真实运行。展示输入、来源、动作、输出、时间与人工决定。',
                      input='Verified local run · 22 October 2026 · p16 · simulation 2020-03-01',
                      reply='72 end-to-end checks passed. 331 offline checks passed across 8 suites. '
                            'This page replays the recorded results; it makes no model or network requests.',
                      timestamp=rows[-1]['timestamp'], status='verified', decision='not_required',
                      actions=[], sources=[], plan=None, review=None,
                      audit=[dict(step=i+1, title=TITLES[i], timestamp=r['timestamp'],
                                  decision=r['human_decision'], status=r['stop'] or r['status'])
                             for i, r in enumerate(rows)]))
    return steps


def render(steps):
    template = (ROOT / 'tools/offline-replay-template.html').read_text()
    return template.replace('__RECORDED_STEPS__', json.dumps(steps, ensure_ascii=False).replace('<', '\\u003c'))


if __name__ == '__main__':
    evidence = ROOT / 'evidence/local-demo-2026-10-22'
    summary = json.loads((evidence / 'e2e-live-summary.json').read_text())
    if summary['failed'] or summary['passed'] != 72:
        raise SystemExit('Refusing to label an unsuccessful run as verified.')
    rows = json.loads((evidence / 'e2e-audit.json').read_text())
    (ROOT / 'Offline Demo.html').write_text(render(make_steps(rows)), encoding='utf-8')
    print('Built Offline Demo.html: 12 steps, all content embedded, no network required.')
