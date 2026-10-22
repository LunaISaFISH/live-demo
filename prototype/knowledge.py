"""
Retrieval over a small local library of published guidance.

The records say what happened to this person; they cannot say what a
reasonable amount of sleep or activity is, or what to do about a sprain.
That comes from documents. `knowledge/*.md` holds five short, sourced
summaries (WHO, AASM/SRS, ACSM, NHS, training-load literature), each
section one retrievable passage carrying its citation and URL.

The library keeps only current guidance. When a newer source covers the
same question, the older one is removed rather than kept alongside it:
ACSM 2011 exercise volumes were superseded by WHO 2020, and the 2016
"safe/danger" workload-ratio bands by the 2020 analyses of their limits.

Plain BM25 over sections, standard library only.
Simplification: lexical BM25 over a few dozen passages; embeddings are the upgrade
if the library grows past what keyword overlap can rank.

Run:  ../.venv/bin/python knowledge.py "how much sleep do adults need"
"""

from __future__ import annotations

import math
import pathlib
import re
from collections import Counter
from functools import lru_cache

LIBRARY = pathlib.Path(__file__).parent / "knowledge"
K1, B = 1.5, 0.75
MAX_RESULTS = 5
RELATIVE_FLOOR = 0.5    # drop passages scoring under half the best match

_STOP = set("""a an and are as at be by can do does for from has have how i if in
into is it its me my of on or should so than that the their them then there these
they this to was we what when where which who why will with you your
get got much many any some just about also am
adult recommend recommended recommendation""".split())   # generic across this library


# The two activity guidelines say "physical activity" and "exercise" for the
# same thing; a query in either words should reach both.
_SYNONYMS = {"exercise": ["activity"], "activity": ["exercise"],
             "workout": ["exercise", "activity"], "training": ["exercise"],
             "run": ["running"], "running": ["run"], "jog": ["running"],
             "injury": ["sprain", "strain"], "sprain": ["injury"],
             "tired": ["fatigue"], "fatigue": ["tired"]}


def _tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    # Crude plural folding so "minutes"/"minute" and "sprains"/"sprain" meet.
    return [w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w
            for w in words if w not in _STOP]


def _parse(path: pathlib.Path) -> list[dict]:
    text = path.read_text()
    _, head, body = text.split("---", 2)
    meta = dict(line.split(": ", 1) for line in head.strip().splitlines())
    passages = []
    for block in re.split(r"^## ", body, flags=re.M)[1:]:
        heading, _, content = block.partition("\n")
        content = " ".join(content.split())
        passages.append({
            "id": f"{path.stem}#{len(passages) + 1}",
            "document": meta["title"], "section": heading.strip(),
            "text": content, "publisher": meta["publisher"],
            "citation": meta["citation"], "url": meta["url"]})
    return passages


@lru_cache(maxsize=1)
def _index():
    passages = [p for f in sorted(LIBRARY.glob("*.md")) for p in _parse(f)]
    # A section heading counts once more than body text: it names the topic.
    # Document titles are left out; shared words like "recommended adult"
    # would otherwise pull every section of one document up together.
    docs = [_tokens(f"{p['section']} {p['section']} {p['text']}") for p in passages]
    df = Counter(t for d in docs for t in set(d))
    avg = sum(map(len, docs)) / len(docs)
    return passages, [Counter(d) for d in docs], [len(d) for d in docs], df, avg


def search(query: str, k: int = 3) -> list[dict]:
    """The best-matching passages, each with its source; [] if nothing matches."""
    passages, freqs, lengths, df, avg = _index()
    n = len(passages)
    terms = set(_tokens(query))
    terms |= {x for t in list(terms) for x in _SYNONYMS.get(t, [])}
    scored = []
    for p, tf, length in zip(passages, freqs, lengths):
        score = 0.0
        for t in terms & tf.keys():
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * tf[t] * (K1 + 1) / (tf[t] + K1 * (1 - B + B * length / avg))
        if score > 0:
            scored.append((score, p))
    scored.sort(key=lambda x: -x[0])
    k = max(1, min(int(k), MAX_RESULTS))
    floor = scored[0][0] * RELATIVE_FLOOR if scored else 0
    return [{**p, "score": round(s, 2)} for s, p in scored[:k] if s >= floor]


def catalogue() -> list[dict]:
    """One row per document, for the page and the tool description."""
    passages = _index()[0]
    seen = {}
    for p in passages:
        seen.setdefault(p["document"], {"document": p["document"],
                                        "publisher": p["publisher"],
                                        "url": p["url"], "sections": 0})
        seen[p["document"]]["sections"] += 1
    return list(seen.values())


def demo() -> None:
    top = lambda q: search(q, 1)[0]["id"]
    assert top("how many hours of sleep does an adult need").startswith("sleep-duration")
    assert "who-2020-physical-activity#1" in [
        h["id"] for h in search("am I doing enough exercise minutes per week", 3)]
    assert top("sprained ankle when can I run again").startswith("nhs-sprains")
    assert top("chest pain dizziness during exercise").startswith("acsm-2015")
    assert top("acute chronic workload ratio spike").startswith("training-load")
    assert search("recommended amount of physical activity per week for adults", 1)[0]["id"] \
        .startswith("who-2020-physical-activity")
    assert search("zzzz quantum banana") == []
    # Superseded guidance stays out: no passage offers ratio bands as thresholds.
    assert not any("0.8 to 1.3" in p["text"] for p in _index()[0])
    print("knowledge: demo passed,", len(_index()[0]), "passages")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        for hit in search(" ".join(sys.argv[1:])):
            print(f"{hit['score']:>6}  {hit['id']}  {hit['section']}\n        {hit['text'][:110]}")
    else:
        demo()
