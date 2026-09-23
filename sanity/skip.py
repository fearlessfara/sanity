"""Inline escapes for false positives on named NL rules.

In a PR body (or any text graded by the judge):

    <!-- sanity-skip-file: smallest-change -->
    <!-- sanity-skip-file -->

Forms:

  sanity-skip-file            whole change, every rule
  sanity-skip-file: id[,id…]  whole change, named rule(s)
"""

import re

# Matches the directive inside a comment or HTML comment body.
DIRECTIVE = re.compile(
    r"sanity-skip(?:-(?P<scope>file|next(?:-line)?))?"
    r"(?:\s*[:=]\s*(?P<ids>[\w.,\s-]+))?",
    re.I,
)

ALL = "*"


def _ids(raw):
    if not raw or not raw.strip():
        return {ALL}
    found = set()
    for part in re.split(r"[\s,]+", raw.strip()):
        part = part.strip().lower().strip("*/")
        if part and part != "*":
            found.add(part)
    return found or {ALL}


def parse_line(text):
    """(scope, frozenset_of_ids) or None.

    scope is "line", "next", or "file".
    """
    match = DIRECTIVE.search(text or "")
    if not match:
        return None
    scope = (match.group("scope") or "line").lower()
    if scope.startswith("next"):
        scope = "next"
    return scope, frozenset(_ids(match.group("ids")))


class SkipMap:
    """Per-line and whole-file skip sets accumulated from a file's lines."""

    def __init__(self):
        self.file_ids = set()
        self.line_ids = {}  # index -> set of ids

    def add_file(self, ids):
        self.file_ids |= set(ids)

    def add_line(self, index, ids):
        self.line_ids.setdefault(index, set()).update(ids)

    def covers(self, rule_id, index=None):
        """True if rule_id (or ALL) is skipped for the whole file or this line."""
        wanted = {ALL, (rule_id or "").lower()}
        if self.file_ids & wanted:
            return True
        if index is None:
            return False
        return bool(self.line_ids.get(index, set()) & wanted)


def collect(lines):
    """Build a SkipMap from source lines (0-based indices)."""
    skips = SkipMap()
    for index, raw in enumerate(lines or []):
        parsed = parse_line(raw)
        if not parsed:
            continue
        scope, ids = parsed
        if scope == "file":
            skips.add_file(ids)
        elif scope == "next":
            skips.add_line(index + 1, ids)
        else:
            skips.add_line(index, ids)
    return skips


def collect_text(text):
    """SkipMap for a PR body or other free text (line-oriented)."""
    return collect((text or "").splitlines())
