# Adjudication rubric: should this person be alerted now?

Each case is one moment in a fitness-monitoring replay. A detector in code
noticed a pattern in a person's wearable and self-report record; the case
gives what the monitor knew (`monitor_facts`, including any earlier reviews
of the same condition and the Alerts already sent in this run) and an
evidence pack read from the record by code (`evidence`: a 14-day reading of
the record and, for a heart-rate event, the 90 minutes of sensor data before
it and the exercise logged that day).

You are an experienced coach with sports-medicine awareness. For each case,
decide: **should the person receive an Alert now?** An Alert is a short
message asking the person to pay attention to something; it is not a
diagnosis. Sending one costs the person's attention, and repeated
near-identical Alerts teach people to ignore them.

Weigh:

- Is the pattern explained by recorded context (walking or exercise just
  before a heart-rate rise, a logged session, an obvious data artefact or
  very thin data)? An explained pattern does not need an Alert.
- How large and how sustained is it, and is the data good enough to trust?
- Has the person already been alerted about the same thing? Alert again only
  when it is clearly worse or newly concerning; a near-repeat of an earlier
  Alert is noise.
- An unexplained, substantial heart-rate rise at rest can matter on the day
  itself; do not wait for it to persist before alerting about a first or
  clearly worse one.
- A long-running shortfall against a guideline (for example WHO weekly
  activity) is worth telling the person about at some point, and again when
  it clearly worsens, not at every look.

Judge only from the case. Do not open any other file.

Output one JSON object per line, one per case, in case order:

    {"id": "p05-01", "should_alert": true, "confidence": "high", "reason": "at most 25 words"}

`confidence` is `high` when you are sure, `low` when the case is borderline.
