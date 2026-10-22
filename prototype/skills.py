"""The coach's skills, in Codex's own format and read by Codex's own mechanism.

Each task's procedure is a skill in `skills/<name>/SKILL.md` with `name` and
`description` frontmatter; shared detail sits in `skills/references/`. Every
conversation gets a copy in its working directory's `.agents/skills`, where
Codex finds the skills, lists them to the model and leaves it to open the
files itself with its shell (progressive disclosure). The shell's sandbox can read that folder and
nothing else: see agent.CodexConversation.
"""
from __future__ import annotations

import pathlib
import re
import shutil

ROOT = pathlib.Path(__file__).parent / "skills"
# Rules a skill cannot do without are written into it at install, from one
# source file: in the pilot runs the model never opened a reference it was
# told to read first.
INCLUDE = re.compile(r"<!-- include: (\S+) -->")


def _frontmatter(text: str) -> dict:
    head = text.split("---", 2)[1] if text.startswith("---") else ""
    return dict(line.split(": ", 1) for line in head.strip().splitlines() if ": " in line)


def catalogue() -> list[dict]:
    """Every skill's name, description and file, from its SKILL.md."""
    out = []
    for path in sorted(ROOT.glob("*/SKILL.md")):
        meta = _frontmatter(path.read_text())
        out.append({"name": meta["name"], "description": meta["description"],
                    "file": str(path.relative_to(ROOT))})
    return out


def install(dest: pathlib.Path) -> pathlib.Path:
    """Copy the skills to `dest`, replacing any older copy."""
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(ROOT, dest)
    for skill in dest.glob("*/SKILL.md"):
        skill.write_text(INCLUDE.sub(lambda m: (ROOT / m.group(1)).read_text().strip(), skill.read_text()))
    return dest
