"""Adapter for Claude Code hook payloads (JSON on stdin)."""

import json
import re
import sys

from .pr import parse_command

NAME = "claude"


def edits(payload):
    return []


def pull_request(payload):
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
    print(message)
    return 0


def followup(message):
    print(json.dumps({
        "decision": "block",
        "reason": message,
    }))
    return 0
