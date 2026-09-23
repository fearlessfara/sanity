"""Adapter for Codex CLI hook payloads (JSON on stdin).

The envelope matches Claude's closely — `tool_name`, `tool_input`, `cwd` — so
the only real work is `apply_patch`, which is how Codex edits files. Its patch
text arrives in `tool_input.command`, the same field Bash uses.
"""

import json
import re
import sys

from .pr import parse_command

NAME = "codex"

BEGIN = "*** Begin Patch"
END = "*** End Patch"
FILE_HEADER = re.compile(r"^\*\*\* (Add|Update|Delete) File: (.+?)\s*$")
MOVE_HEADER = re.compile(r"^\*\*\* Move to: (.+?)\s*$")
HUNK = re.compile(r"^@@")


def parse_patch(patch):
    """[(path, new_lines, added_indices)] for one apply_patch envelope.

    Context lines and added lines together give the file as it will look, so
    a comment added above a line that was already there is still judged
    against the code it sits on.
    """
    files = []
    path = None
    lines = []
    added = set()

    def flush():
        if path and lines:
            files.append((path, list(lines), set(added)))

    for raw in (patch or "").splitlines():
        if raw.startswith(BEGIN) or raw.startswith(END):
            continue

        header = FILE_HEADER.match(raw)
        if header:
            flush()
            path = header.group(2) if header.group(1) != "Delete" else None
            lines, added = [], set()
            continue

        move = MOVE_HEADER.match(raw)
        if move:
            path = move.group(1)
            continue

        if path is None or HUNK.match(raw):
            continue

        if raw.startswith("+"):
            added.add(len(lines))
            lines.append(raw[1:])
        elif raw.startswith("-"):
            continue
        elif raw.startswith(" "):
            lines.append(raw[1:])
        elif not raw:
            lines.append("")

    flush()
    return files


def edits(payload):
    """[(path, new_lines, added_indices)] for an edit-shaped payload."""
    if payload.get("tool_name") != "apply_patch":
        return []
    data = payload.get("tool_input") or {}
    patch = data.get("command") or data.get("patch") or data.get("input") or ""
    if isinstance(patch, list):
        patch = "\n".join(str(part) for part in patch)
    return parse_patch(patch)


def pull_request(payload):
    """(kind, title, body, flags) or None."""
    tool = payload.get("tool_name", "")
    data = payload.get("tool_input") or {}

    if tool in ("Bash", "shell", "local_shell"):
        command = data.get("command")
        if isinstance(command, list):
            command = " ".join(str(part) for part in command)
        return parse_command(command or "")

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
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": message,
        }
    }))
    print(message, file=sys.stderr)
    return 2


def advise(message):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": message,
        }
    }))
    return 0
