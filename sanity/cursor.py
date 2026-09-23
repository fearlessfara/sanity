"""Adapter for Cursor hook payloads (JSON on stdin).

Verified against Cursor 3.21.16. Two things are particular to Cursor:

Every edit tool is reported as `tool_name: "Write"` with the complete new
file in `tool_input.content` — there is no old-vs-new in the payload. The
lines the edit actually adds are recovered by diffing against the copy still
on disk, which `preToolUse` is early enough to read.

Events are split by kind rather than by tool: file edits arrive on
`preToolUse`, shell commands on `beforeShellExecution` with the command at
the top level, MCP calls on `beforeMCPExecution`. Those three can deny;
`afterFileEdit` cannot, so nothing is hung off it.
"""

import json
import sys

from .comments import added_indices, added_vs_file
from .pr import parse_command

NAME = "cursor"


def _input(payload):
    data = payload.get("tool_input")
    return data if isinstance(data, dict) else payload


def edits(payload):
    """[(path, new_lines, added_indices)] for an edit-shaped payload."""
    data = _input(payload)
    path = data.get("file_path") or payload.get("file_path")
    if not path:
        return []

    batch = payload.get("edits") or data.get("edits")
    if isinstance(batch, list) and batch:
        out = []
        for edit in batch:
            if not isinstance(edit, dict):
                continue
            new = (edit.get("new_string") or "").splitlines()
            if new:
                out.append(
                    (path, new, added_indices(edit.get("old_string") or "", new))
                )
        return out

    content = data.get("content")
    if not isinstance(content, str):
        return []
    new = content.splitlines()
    return [(path, new, added_vs_file(path, new))]


def pull_request(payload):
    """(kind, title, body, flags) or None."""
    data = _input(payload)

    command = payload.get("command") or data.get("command")
    if isinstance(command, str) and command:
        return parse_command(command)

    name = payload.get("tool_name") or ""
    if "merge_request" in name:
        return "gitlab", data.get("title"), data.get("description"), set()
    if "pull_request" in name:
        return (
            "github",
            data.get("title"),
            data.get("body"),
            {"draft"} if data.get("draft") else set(),
        )
    return None


def refuse(message):
    print(json.dumps({
        "permission": "deny",
        "agent_message": message,
        "user_message": message.splitlines()[0] if message else "Blocked by sanity.",
    }))
    print(message, file=sys.stderr)
    return 2


def advise(message):
    print(json.dumps({
        "permission": "allow",
        "agent_message": message,
    }))
    return 0
