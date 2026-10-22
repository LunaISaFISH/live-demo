"""HTTP/tool regressions for computed statistics."""
import datetime as dt
import json
import threading
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer

import anyio
import agent
import observe
import plan
from profile import Profile
import server


def main():
    p = Profile('p16')
    day = dt.date(2020, 3, 1)
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = (f'http://127.0.0.1:{httpd.server_port}/api/inspect?pid=p16&as_of=2020-03-01'
           '&domain=sleep&start=2020-02-24&end=2020-03-01'
           '&compare_to_start=2020-02-17&compare_to_end=2020-02-23')
    try:
        with urllib.request.urlopen(url) as response:
            assert 'statistics' not in json.load(response)
        try:
            urllib.request.urlopen(url + '&include_statistics=perhaps')
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
        else:
            raise AssertionError('invalid statistics option was silently accepted')
        with urllib.request.urlopen(url + '&include_statistics=true') as response:
            result = json.load(response)['statistics']
        assert result['current']['calendar_days'] == 7
        assert result['current']['recorded_days'] == 6
        assert result['current']['missing_days'] == 1
        assert result['current']['coverage_percent'] == 85.71
        assert result['comparison']['median_efficiency']['difference'] == -2.5
        assert result['comparison']['median_efficiency']['unit'] == 'percentage_points'
        assert result['comparison']['median_hours']['difference_minutes'] == -7.2
        assert result['overlap_calendar_days'] == 0
        assert result['sleep_balance']['debt_hours'] == p.snapshot(day).sleep_debt()['debt_hours']
        assert result['sleep_balance']['nights'] == 6
        print('PASS HTTP parameter returns exact coverage, deltas, units and recorded-sleep balance')
        with urllib.request.urlopen(f'http://127.0.0.1:{httpd.server_port}/api/snapshot?pid=p16&as_of=2020-03-01') as response:
            headline = json.load(response)['domains']['sleep']['headline']
        assert '2.0 hours below' in headline and '+2.0' not in headline
        print('PASS HTTP headline states the direction of sleep shortfall explicitly')
    finally:
        httpd.shutdown()
        httpd.server_close()

    gap = observe.inspect(p.snapshot(day), 'training', day, day,
                          (dt.date(2020, 1, 6), dt.date(2020, 2, 3)), include_statistics=True)
    assert gap['statistics']['current']['recorded_days'] == 0
    assert gap['statistics']['comparison']['per_week']['difference'] is None
    overlapping = observe.inspect(p.snapshot(day), 'sleep', dt.date(2020, 2, 24), day,
                                  (dt.date(2020, 2, 20), dt.date(2020, 2, 25)), include_statistics=True)
    assert overlapping['statistics']['overlap_calendar_days'] == 2
    print('PASS missing evidence is not a zero-valued comparison; overlapping dates are explicit')

    s = agent.Session('p16', p, clock=day)
    tools = {t.name: t for t in agent.build_tools(s)}
    assert 'include_statistics' not in tools['read_record'].input_schema['properties']
    history = json.loads(anyio.run(tools['read_record'].handler, {'days': 7})['content'][0]['text'])
    assert history['domains']['sleep']['this_window']['calendar_days'] == 7
    assert history['domains']['sleep']['previous_window']['calendar_days'] == 7
    print('PASS statistics are always computed by the tool; there is no option to skip them')


def wellness_words():
    """The self-report scales read the right way round, checked on the data itself."""
    import statistics
    import profile as prof
    from profile import ROOT
    reports = [r for d in ROOT.glob('p??') for r in Profile(d.name).reports]
    sore = [r.soreness for r in reports if r.soreness is not None and r.soreness_area_codes]
    free = [r.soreness for r in reports if r.soreness is not None and not r.soreness_area_codes]
    assert statistics.mean(sore) < prof.WELLNESS_NORMAL < statistics.mean(free) + 0.5, (statistics.mean(sore), statistics.mean(free))
    pairs = [(r.readiness, r.fatigue) for r in reports if r.readiness and r.fatigue is not None]
    xs, ys = zip(*pairs)
    mx, my = statistics.mean(xs), statistics.mean(ys)
    assert sum((x - mx) * (y - my) for x, y in pairs) > 0          # fresher goes with readier
    assert prof.wellness_words('soreness', 1) == 'much more sore than normal'
    assert prof.wellness_words('fatigue', 5) == 'much fresher than normal'
    assert prof.wellness_words('readiness', 3) == 'low readiness to train'
    assert prof.wellness_words('readiness', 4) == 'moderate readiness to train'
    print('PASS a low soreness score is a sorer day and a high fatigue score a fresher one, as the data show')

    p05 = Profile('p05')
    early = [r for r in p05.reports if r.date < dt.date(2019, 12, 20)]
    assert early and all(r.readiness is None for r in early[1:]) and any(r.fatigue for r in early)
    assert not any(s.kind == 'readiness' for day in (dt.date(2019, 12, 8), dt.date(2019, 12, 15))
                   for s in __import__('monitor').signals_on(p05.snapshot(day), day))
    assert plan.daily_capacity(p05.snapshot(dt.date(2019, 12, 15)))[0] != 'rest'
    p06 = [r.readiness for r in Profile('p06').reports]
    assert 0.0 in p06          # a single genuine 0 is kept
    print('PASS an answer left at the end of its scale for weeks is not read as a rating; a single one is')

    text = json.dumps(observe.inspect(Profile('p16').snapshot(dt.date(2020, 3, 1)), 'recovery',
                                      dt.date(2020, 2, 16), dt.date(2020, 3, 1), include_statistics=True))
    assert 'than normal' in text and 'median_fatigue' not in text and '1-10' not in text
    print('PASS recovery history reaches the coach in words, with no scale to interpret')

    # A record younger than 28 days: its usual week is its load over the weeks
    # it covers, so p12's first fortnight is not a load spike.
    p12 = Profile('p12')
    day = dt.date(2019, 11, 17)
    cut = p12.snapshot(day)
    load = cut.training_load(day)
    covered = (day - cut.first_day).days + 1
    assert abs(load['chronic_28d_per_week']
               - round(sum(x.load for x in cut.sessions if x.load is not None) / (covered / 7), 1)) < 0.2
    print('PASS a young record averages its load over the weeks it covers, not over four')

    # Load is the person's own rating x minutes; a device-detected session has
    # no rating, so it carries none, and its minutes are reported on their own.
    p05 = Profile('p05')
    assert all(x.load is None for x in p05.sessions if x.sources == ('device',))
    assert all(x.load == x.rpe * x.minutes for x in p05.sessions if x.rpe is not None)
    tl = p05.training_load(dt.date(2020, 2, 10))
    assert 'unrated_device_minutes_7d' in tl and 'no rating' in tl['load_is']
    print('PASS a device-detected session carries no invented load')

    # The Fitbit export repeats a night (same logId) at monthly file boundaries.
    for pid in ('p05', 'p16'):
        dates = [n.date for n in Profile(pid).nights]
        assert len(dates) == len(set(dates)), pid
    print('PASS a night repeated in the export is counted once')

    # The WHO comparison rests on daily activity minutes, so its coverage is
    # the share of days with them, not of days with a logged session (p16: 3%).
    adq = [x for x in observe._snap_training(Profile('p16'), dt.date(2020, 2, 20))
           if x['topic'] == 'training.adequacy'][0]
    assert adq['data_quality']['coverage'] >= 0.9, adq['data_quality']
    print('PASS activity against WHO reports the coverage of the activity minutes it uses')


if __name__ == '__main__':
    main()
    wellness_words()
