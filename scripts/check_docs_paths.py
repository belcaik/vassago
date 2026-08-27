#!/usr/bin/env python3
"""Verify that every repo path named in the Markdown documentation exists.

Two kinds of reference are checked:

- Markdown links `[text](target)`, resolved relative to the file that holds
  them. URLs and pure `#anchor` targets are skipped; a `#fragment` on a file
  target is stripped before resolving.
- Inline code spans that name a repo path (`caldir/...`, `docs/...`, and the
  other known top-level entries, plus a short list of root filenames),
  resolved relative to the repository root.

Runtime directories are gitignored, so paths under them are expected to be
absent from a clean checkout and are skipped. So are absolute paths, which
name locations inside the container rather than in the repo.

Exit 0 with a summary line, or 1 listing every missing target.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

EXTRA_DOCS = [
    "AGENTS.md",
    "README.md",
    "CLAUDE.md",
]

# Top-level entries a code span must start with to be treated as a repo path.
KNOWN_ROOTS = {
    ".claude",
    ".github",
    "caldir",
    "docs",
    "radicale",
    "scripts",
    "tests",
    "u-crawler",
}

KNOWN_ROOT_FILES = {
    ".env.example",
    ".gitignore",
    "AGENTS.md",
    "CLAUDE.md",
    "LICENSE",
    "README.md",
    "compose.yaml",
}

# Gitignored runtime state: absent from a clean checkout by design.
RUNTIME_PREFIXES = (
    "caldir/config/",
    "caldir/state/",
    "caldir/calendars/",
    "radicale/data/",
    "radicale/config/users",
    "u-crawler/",
)

URL_SCHEMES = ("http://", "https://", "mailto:")

LINK_RE = re.compile(r"(?<!!)\[[^\]\n]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
CODE_SPAN_RE = re.compile(r"`([^`\n]+)`")
PATH_SPAN_RE = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+$")


def documents():
    seen = []

    docs_dir = REPO_ROOT / "docs"
    if docs_dir.is_dir():
        seen.extend(sorted(docs_dir.rglob("*.md")))

    for name in EXTRA_DOCS:
        path = REPO_ROOT / name
        if path.is_file():
            seen.append(path)

    skills_dir = REPO_ROOT / ".claude" / "skills"
    if skills_dir.is_dir():
        seen.extend(sorted(skills_dir.rglob("SKILL.md")))

    return seen


def is_runtime(relative: str) -> bool:
    # A directory may be named with or without its trailing slash.
    return relative.startswith(RUNTIME_PREFIXES) or (
        relative + "/"
    ).startswith(RUNTIME_PREFIXES)


def link_targets(text: str):
    for target in LINK_RE.findall(text):
        if target.startswith("#"):
            continue
        if target.lower().startswith(URL_SCHEMES):
            continue
        yield target.split("#", 1)[0]


def span_targets(text: str):
    for span in CODE_SPAN_RE.findall(text):
        span = span.strip()
        if span in KNOWN_ROOT_FILES:
            yield span
            continue
        if span.startswith("/"):
            continue
        if not PATH_SPAN_RE.match(span):
            continue
        if span.split("/", 1)[0] not in KNOWN_ROOTS:
            continue
        yield span


def check(docs):
    """Return (problems, number of references checked)."""
    problems = []
    checked = 0

    for document in docs:
        text = document.read_text(encoding="utf-8")
        here = document.relative_to(REPO_ROOT)

        for target in link_targets(text):
            if not target:
                continue
            checked += 1
            resolved = (document.parent / target).resolve()
            try:
                relative = resolved.relative_to(REPO_ROOT).as_posix()
            except ValueError:
                problems.append(f"{here}: link escapes the repo: {target}")
                continue
            if is_runtime(relative):
                continue
            if not resolved.exists():
                problems.append(f"{here}: broken link -> {target}")

        for target in span_targets(text):
            if is_runtime(target):
                continue
            checked += 1
            if not (REPO_ROOT / target).exists():
                problems.append(f"{here}: code span names a missing path -> {target}")

    return problems, checked


def main() -> int:
    docs = documents()
    problems, checked = check(docs)

    if problems:
        print("[docs-paths] missing targets:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    print(
        f"[docs-paths] ok: {checked} references in {len(docs)} document(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
