# Label audit: is the reference label right?

Each packet is one monitoring review where the reference is in doubt: either
the two blind raters split, or the coach decided against the label both
raters agreed on. The packet holds what the raters saw, both raters' labels
and reasons, the coach's full review, and the outputs of the tools the coach
called (regenerated from the record).

Answer the rubric's question for this case: **should the person receive an
Alert now?** The rubric is `../RUBRIC.md`; read it first.

1. Check facts before judging. For every factual claim that bears on the
   decision, in the raters' reasons and in the coach's review (activity
   before a rise, whether exercise was logged, heart-rate levels, what was
   already alerted, how a condition changed), look it up in the packet and
   note any that are wrong.
2. Then decide. Neither side is authority: the coach's reasoning is an
   argument to check, not evidence, and the raters saw less data than the
   coach did. Use "ambiguous" only when, after checking, the rubric
   genuinely supports both answers.

Output one JSON line per packet:

    {"packet": "<run>/<id>", "kind": "split" | "against_agreed",
     "agreed_label": true | false | null, "coach_alert": true | false,
     "final_should_alert": true | false | null,
     "verdict": "label stands" | "label wrong" | "resolved" | "ambiguous",
     "wrong_facts": [{"who": "raterA" | "raterB" | "coach", "claim": "...", "data_shows": "..."}],
     "reason": "at most 40 words"}

`verdict`: for a split packet use "resolved" (with final_should_alert true or
false) or "ambiguous"; for an against-agreed packet use "label stands",
"label wrong" or "ambiguous".
