---
name: monitoring
description: Reviewing a candidate signal from background monitoring, in a turn that begins with [monitoring event].
---

# Reviewing a monitoring signal

1. Investigate the signal with `read_record`, which is sealed at the event
   boundary, as `../references/reading-the-record.md` describes. For an
   intraday signal, `inspect_sensor_window` can read the current day only
   through the event time while daily history ends on the previous day:
   read what led up to the event (heart rate, steps, logged exercise) far
   enough back to see the person's state before the rise began, so that
   the end of any earlier activity is in view, as well as the days before
   it. Decide how far afresh for each event, from what this one shows,
   not from the window you used for an earlier one. Nobody is present
   to answer questions, so do not ask any, and leave your workspace
   unchanged: memory and plans change only in a conversation.
2. Decide what accounts for it. Something else in the record that accounts
   for what you see - a logged or obviously active session, short sleep,
   alcohol, a hard week, a recent injury or illness - makes it explained.
   The signal's own measure restated is not an explanation: low weekly
   activity is not explained by few or short sessions, and a raised heart
   rate is not explained by the heart rate being high. If nothing else
   accounts for it, it is unexplained; if too little is recorded to look,
   it is unclear. Call it unexplained only when what you have read could
   have shown an explanation if there were one; if it could not, read more
   first.
3. Decide whether the person should be alerted now. Weigh what the signal
   is, how far it is from their usual, how long it has lasted and how
   well the record covers it - the facts in the event are for you to
   weigh, not cut-offs.
   - A short event can matter most. A steep heart-rate rise at rest with
     no activity around it is worth an Alert on the day, however brief;
     waiting for it to repeat is no use to the person.
   - A continuing condition comes with its history: when it started, how it
     looked then and now, and your earlier reviews of it. One that has gone
     on for weeks without anything accounting for it, or has clearly got
     worse since you last looked, is worth an Alert, so the person knows.
     The person can always see it on their page; an Alert is for when it
     needs their attention now.
   - Do not alert for something ordinary context accounts for, or for a
     mild, brief blip with a plain reason.
   - Do not repeat an Alert already raised in this run for the same
     concern unless it has clearly got worse; the event lists them. A
     change of a beat or two, or a few minutes, is not worse.
4. For a continuing condition, choose when to look at it again if it is
   still going (`recheck_in_days`): soon if it could turn serious, later if
   it is slow and steady, null if nothing more about it would change your
   decision while it lasts. For a one-off event it is null.
5. Call `submit_signal_review` with the verdict, the recorded observations
   that support it, the limitations of what the record could show, `alert`,
   a one-sentence `alert_reason` and `recheck_in_days`.
6. End with a final answer: a short note for the person on what the review
   found and what remains unknown. When you alert on a heart-rate event
   with nothing to account for it, say that if they had or have chest pain,
   fainting, severe breathlessness or palpitations they should stop and
   seek urgent medical care; a recording cannot tell whether they did.
