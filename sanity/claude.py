"""Adapter for Claude Code hook payloads (JSON on stdin).

Claude reads a refusal off stderr when the hook exits 2, so nothing needs to
be written to stdout.
"""

import re
import sys

from .comments import added_indices, added_vs_file
from .pr import parse_command

NAME = "claude"


def edits(payload):
    """[(path, new_lines, added_indices)] for an edit-shaped payload.

    `added_indices` of None means every line counts as added, which is right
    for a file that does not exist yet.
    """
    tool = payload.get("tool_name", "")
    data = payload.get("tool_input") or {}
    path = data.get("file_path") or data.get("notebook_path") or ""
    if not path:
        return []

    if tool == "Write":
        new = (data.get("content") or "").splitlines()
        return [(path, new, added_vs_file(path, new))]

    if tool in ("Edit", "NotebookEdit"):
        new = (data.get("new_string") or data.get("new_source") or "").splitlines()
        old = data.get("old_string") or data.get("old_source") or ""
        return [(path, new, added_indices(old, new))]

    if tool == "MultiEdit":
        out = []
        for edit in data.get("edits") or []:
            new = (edit.get("new_string") or "").splitlines()
            out.append((path, new, added_indices(edit.get("old_string") or "", new)))
        return out

    return []


def pull_request(payload):
    """(kind, title, body, flags) or None."""
    tool = payload.get("tool_name", "")
    data = payload.get("tool_input") or {}

    if tool == "Bash":
        return parse_command(data.get("command") or "")

    if re.search(r"(create|update)_pull_request$", tool):
        return (
            "github",
            data.get("title"),
            data.get("body"),
            {"draft"} if data.get("draft") else set(),
        )

    if re.search(r"(create|update)_merge_request$", tool):
        return "gitlab", data.get("title"), data.get("description"), set()

    return None


def refuse(message):
    print(message, file=sys.stderr)
    return 2


def advise(message):
    """Non-blocking note for the model (session judge)."""
    print(message)
    return 0
