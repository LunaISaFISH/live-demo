"""Display records from tool results: numbers come from the server, not from the model's answer."""
from __future__ import annotations

import datetime as dt
import json

from profile import MODALITY


# What the person reads; tool output keeps the machine names for the Agent.
_COVERAGE_NOTE = ('Coverage counts days with at least one record out of all calendar days. '
                  'It describes what was recorded, not whether that explains anything.')


def _unit(value) -> str:
    return str(value or '').replace('percentage_points', '% points').replace('_', ' ')


def _activity(value) -> str:
    label = MODALITY.get(value, {}).get('label', str(value))
    return label[:1].upper() + label[1:]


def _weekday(value) -> str:
    try:
        return dt.date.fromisoformat(str(value)).strftime('%a')
    except ValueError:
        return ''


def build_sections(executions: list[dict], draft: dict | None = None, cited=()) -> list[dict]:
    """Use server-produced values; never parse numbers out of an assistant reply."""
    sections = []

    def add(title, columns, rows, context='', notes=None):
        if rows:
            sections.append({'title': title, 'context': context, 'columns': columns,
                             'rows': rows, 'notes': notes or []})

    def window(value):
        return f"{value.get('start', '')} to {value.get('end', '')}" if value else ''

    # Exact retries should not produce duplicate tables.
    unique = {}
    for call in executions:
        if call.get('result', {}).get('is_error'):
            continue
        try:
            data = json.loads(call['result']['content'][0]['text'])
        except (KeyError, ValueError, IndexError, TypeError):
            continue
        if isinstance(data, dict):
            key = (call['name'], json.dumps(call['input'], sort_keys=True))
            unique.pop(key, None)
            unique[key] = data

    for (name, _), data in unique.items():
        if name == 'read_record':
            this_window = window(data['window'])
            add('Record coverage', ['Domain', 'Days with records', 'Previous window'],
                [[d.capitalize(), f"{b['this_window']['days_with_records']} of {b['this_window']['calendar_days']}",
                  (f"{b['previous_window']['days_with_records']} of {b['previous_window']['calendar_days']}"
                   if b['previous_window'] else '')]
                 for d, b in data['domains'].items()],
                this_window + (' vs ' + window(data['previous_window']) if data['previous_window'] else ''),
                [_COVERAGE_NOTE])
            # Only what the answer cites gets a table: a sleep answer shows sleep.
            for d, b in data['domains'].items():
                if b.get('card_id') not in cited:
                    continue
                before, change = b['previous_window'] or {}, b.get('change', {})
                rows = [[k.replace('_', ' '), v, before.get(k), change.get(k), _unit(b['units'].get(k, ''))]
                        for k, v in b['this_window'].items()
                        if k not in ('days_with_records', 'calendar_days') and not isinstance(v, (dict, list))]
                add(d.capitalize() + ' records', ['Measure', 'This window', 'Previous window', 'Change', 'Unit'],
                    rows, this_window, ['Change is this window minus the previous one; it does not establish a cause.'])
                balance = b.get('balance_against_own_usual')
                if balance:
                    add('Sleep against own usual', ['Measure', 'Value', 'Unit'], [
                        ['Recorded nights', balance.get('nights'), 'nights'],
                        ['Shortfall (negative means surplus)', balance.get('debt_hours'), 'hours']],
                        this_window, ['Missing nights are not counted as zero sleep.'])
            state = data['state_at_window_end']
            if any((state.get(k) or {}).get('card_id') in cited for k in state if isinstance(state.get(k), dict)):
                rows = [[label, (state.get(group) or {}).get(key), unit] for label, group, key, unit in [
                    ('Intensity ceiling', 'capacity_today', 'ceiling', ''),
                    ('Recorded sessions, past week', 'training_load', 'sessions_7d', 'sessions'),
                    ('Training load, past week', 'training_load', 'acute_7d', 'sRPE'),
                    ('Typical weekly load, past month', 'training_load', 'chronic_28d_per_week', 'sRPE/week'),
                    ('Recent-to-baseline load ratio', 'training_load', 'acwr', 'ratio'),
                    ('Sleep shortfall, past week (negative means surplus)', 'sleep_debt_7d', 'debt_hours', 'hours'),
                    ('Readiness to train, latest report', 'readiness_trend', 'latest', ''),
                    ('Median weekly moderate/vigorous activity', 'activity_vs_who', 'median_mvpa_minutes', 'min/week'),
                    ('Median daily steps', 'ambient_activity', 'median_daily_steps', 'steps/day'),
                    ('Days since the last logged injury', 'recent_injuries', 'days_since_last', 'days')]]
                add('State on ' + state['as_of'], ['Measure', 'Value', 'Unit'], rows, this_window,
                    state['capacity_today']['cautions'])
        elif name == 'search_guidelines':
            add('Guidance retrieved', ['Source', 'Passage'],
                [[f"{p['publisher']} - {p['section']}", p['text']] for p in data.get('passages', [])],
                'Query: ' + data.get('query', ''),
                sorted({p['citation'] + ' ' + p['url'] for p in data.get('passages', [])}))
        elif name == 'inspect_sensor_window':
            units = {'mean_hr_bpm': 'bpm', 'max_hr_bpm': 'bpm', 'steps': 'steps',
                     'distance_km': 'km', 'calories': 'kcal', 'buckets': 'buckets', 'heart_buckets': 'buckets',
                     'implausible_calorie_buckets': 'buckets'}
            add('Sensor window', ['Measure', 'Value', 'Unit'],
                [[k.replace('_', ' '), v, units.get(k, '')] for k, v in data.get('summary', {}).items()],
                window(data.get('window')), data.get('limitations'))

    # The draft as the turn left it, read from the workspace, never from the reply's prose.
    if draft and draft['draft'] in ('created', 'revised'):
        revised = draft['draft'] == 'revised'
        change = {'added': 'new', 'changed': 'changed', 'same': '', 'removed': 'removed'}
        # "Fri 6 Mar": one short column, so the table fits the chat; the context gives the year.
        day = lambda v: f"{_weekday(v)} {dt.date.fromisoformat(v).day} {dt.date.fromisoformat(v):%b}"
        add('Plan draft', ['Day', 'Activity', 'Minutes', 'Intensity'] + ['Change'] * revised,
            [[day(r['date']), _activity(d['activity']), d['minutes'], d['intensity']]
             + [change[r['status']]] * revised
             for r in draft['draft_diff'] for d in [r['after'] or r['before']]],
            f"From {draft['draft_diff'][0]['date']}. Save it on the plan panel, or ask for changes"
            if draft['draft_diff'] else 'Save it on the plan panel, or ask for changes')
    return sections
