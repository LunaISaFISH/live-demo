# Monitoring evaluation: were the Alerts right?

Run 2026-10-05 (gpt-5.6-terra, medium effort). PMData has no
labels saying when a person should have been alerted, so the reference is
built here.

## Evaluation basis

Three repetitions of the same system replayed p05, p12 and p16
(446 participant-days per repetition). Two blind model raters (separate runs of Claude Opus 5.5) labelled
whether each review warranted an Alert; a third Claude Opus 5.5 pass (`label-audit/`), reviewed by
the group, resolved disagreements and checked decisions against the underlying records.
It saw the coach's answer and was not blind. Labels come from the model raters and that
review, not clinical ground truth. Repeated reviews are not independent patient observations.

## Results

| Run | Reviews | Alerts | Precision | Recall | F1 |
| --- | --- | --- | --- | --- | --- |
| 1 | 119 | 31 | 0.65 | 0.83 | 0.73 |
| 2 | 113 | 28 | 0.68 | 0.79 | 0.73 |
| 3 | 103 | 20 | 0.75 | 0.71 | 0.73 |
| Mean | 112 | 26 | 0.69 | 0.78 | 0.73 |

Pooled over the three runs (335 reviews): 54 Alerts right, 25 that should not
have been sent, 15 missed, 241 correctly not sent. Pooled precision is
54/79 = 0.68, recall 54/69 = 0.78, and F1 = 0.73; the table reports
per-run values and their arithmetic means. About 1.8 Alerts a month
per person.

Of the 25 Alerts that should not have been sent, 18 were repeats on a recheck
of a Condition the person had already been alerted about (readiness 6, load
ratio 5, weekly activity 4, resting heart rate 3); 5 were heart-rate events
and 2 were new resting-heart-rate Conditions. Of the 15 missed, 6 were sleep
debt.

**Consistency.** The 46 heart-rate events are the same in every run; the
coach made the same decision in all three runs on 40 of them
(`eval_alerts.py replicas`). Runs differ more on Conditions, because each
run chooses its own recheck days.

**Grounding.** The reviews stated 4,427 numbers. All but 33 were in that
review's own tool outputs or the facts the monitor handed it; 31 of the 33
were in an earlier review's outputs in the same conversation, and the other
two are the same correct sum (1,079 steps from 20:40 to 20:55 on 2020-02-19:
145 + 375 + 483 + 76).

**Fact-check of the 79 Alerts.** 1,120 statements: 1,080 supported (96%), 17
not found in that review's data, 23 contradicted (2.1%), in 16 Alerts. The
contradictions are small: day counts off by one ("eight days earlier" for a
review seven days before), a value quoted for the wrong bucket or bucket
size, and counts off by one. Two verdicts did not fit their own notes
(run 2, p12-18 and p12-20: "explained" with a note saying the record cannot
establish the reason). Five messages said more than the record warranted,
three of them telling p12 to stop exercising and seek medical advice over
minor injury entries.

## Limits

The reference comes from language models using our rubric, with disputed
cases settled by a third model pass that the group reviewed, not from
clinicians or coaches, and three people from one dataset say nothing about
other populations. Whether a person would act on these Alerts was not
measured.
