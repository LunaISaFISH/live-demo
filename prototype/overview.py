"""
participant-overview.xlsx, read with the standard library only.

The runtime cannot take an openpyxl dependency, and an .xlsx is just a
zip of XML, so this parses it with `zipfile` and `xml.etree`. It reads
the small workbook once and returns, per participant, a validated
metadata record plus field-level quality warnings.

The metadata is CONTEXT, not a rule. A valid maximum heart rate may
contextualise the heart-rate zones; age, gender, cohort, the 5 km time
and stride length are recorded with their warnings and are never turned
into a plan constraint. Invalid, missing and text-placeholder values
(the sheet has cells reading "november" and "injured" where a number
belongs) are kept out of the metadata and surfaced as warnings instead.

The raw workbook is never modified.

Run:  ../.venv/bin/python overview.py
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from functools import lru_cache
from pathlib import Path

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

# Header text -> the field we store it under. Read from the header row so
# a column reorder does not silently misread.
_HEADER_FIELD = {
    "participant id": "pid", "age": "age", "height": "height",
    "gender": "gender", "a or b person": "cohort",
    "max heart rate": "max_hr", "minutes": "run5k_min",
    "seconds": "run5k_sec", "stride walk": "stride_walk",
    "stride run": "stride_run", "1st 5km run": "run5k_date",
}

# Plausibility bounds for the numeric fields. Outside these the value is
# treated as suspicious and dropped to a warning rather than used.
_BOUNDS = {"age": (10, 90), "height": (120, 220), "max_hr": (100, 230),
           "run5k_min": (8, 45), "run5k_sec": (0, 59),
           "stride_walk": (30, 200), "stride_run": (30, 250)}


def _cells(path: Path) -> dict[int, dict[str, str]]:
    """Return {row_number: {column_letter: resolved_value}}."""
    z = zipfile.ZipFile(path)
    shared = ["".join(t.text or "" for t in si.iter(_NS + "t"))
              for si in ET.fromstring(z.read("xl/sharedStrings.xml"))]
    sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    rows: dict[int, dict[str, str]] = {}
    for c in sheet.iter(_NS + "c"):
        ref = c.get("r")
        col = re.match(r"[A-Z]+", ref).group()
        rownum = int(re.search(r"\d+", ref).group())
        v = c.find(_NS + "v")
        val = v.text if v is not None else None
        if c.get("t") == "s" and val is not None:
            val = shared[int(val)]
        if val is not None:
            rows.setdefault(rownum, {})[col] = val
    return rows


def _coerce(field: str, raw):
    """Return (value, warning). One of them is always None."""
    if raw is None or str(raw).strip() == "":
        return None, "missing"
    text = str(raw).strip()
    if field in ("gender",):
        g = text.lower()
        return (g, None) if g in ("male", "female") else (None, f"unexpected {text!r}")
    if field in ("cohort",):
        return (text, None) if text in ("A", "B") else (None, f"unexpected {text!r}")
    if field in ("pid", "run5k_date"):
        return text, None
    try:
        num = float(text)
    except ValueError:
        return None, f"non-numeric {text!r}"
    lo, hi = _BOUNDS.get(field, (float("-inf"), float("inf")))
    if not lo <= num <= hi:
        return None, f"out of range ({num})"
    return num, None


@lru_cache(maxsize=1)
def _table(path_str: str) -> dict[str, dict]:
    rows = _cells(Path(path_str))
    header = rows.get(2, {})
    col_field = {col: _HEADER_FIELD[txt.strip().lower()]
                 for col, txt in header.items()
                 if txt.strip().lower() in _HEADER_FIELD}
    out: dict[str, dict] = {}
    for rownum in sorted(rows):
        if rownum <= 2:
            continue
        row = rows[rownum]
        pid = row.get(next((c for c, f in col_field.items()
                            if f == "pid"), ""), "")
        if not re.fullmatch(r"p\d\d", str(pid).strip()):
            continue
        meta, warn = {}, {}
        for col, field in col_field.items():
            if field == "pid":
                continue
            value, warning = _coerce(field, row.get(col))
            if warning:
                warn[field] = warning
            else:
                meta[field] = value
        out[pid.strip()] = {"metadata": meta, "warnings": warn}
    return out


def for_pid(pid: str, root: Path | None = None) -> dict:
    """Validated overview metadata and warnings for one participant.

    Returns {"metadata": {...}, "warnings": {field: reason}}. Absent
    participant or unreadable workbook yields empty metadata with a single
    warning, so callers always get the same shape.
    """
    path = (root or Path(__file__).parent.parent / "data" / "pmdata") \
        / "participant-overview.xlsx"
    if not path.exists():
        return {"metadata": {}, "warnings": {"overview": "workbook missing"}}
    try:
        return _table(str(path)).get(
            pid, {"metadata": {}, "warnings": {"overview": "not in workbook"}})
    except Exception as exc:                       # never fail a Profile load
        return {"metadata": {}, "warnings": {"overview": f"unreadable: {exc}"}}


def demo() -> None:
    from profile import PARTICIPANTS
    for pid in PARTICIPANTS:
        o = for_pid(pid)
        m, w = o["metadata"], o["warnings"]
        print(f"{pid}  age={m.get('age')} h={m.get('height')} "
              f"sex={m.get('gender')} cohort={m.get('cohort')} "
              f"maxHR={m.get('max_hr')}"
              + (f"  warnings: {w}" if w else ""))

    # Self-check: the validator classifies each defect kind.
    assert _coerce("age", "")[1] == "missing"
    assert _coerce("age", "november")[1].startswith("non-numeric")
    assert _coerce("max_hr", "500")[1].startswith("out of range")
    assert _coerce("age", "34") == (34.0, None)
    assert _coerce("gender", "male ") == ("male", None)
    print("\nvalidator self-check passed")


if __name__ == "__main__":
    demo()
