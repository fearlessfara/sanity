"""Adapter for Cursor hook payloads (JSON on stdin)."""

import json
import sys

from .pr import parse_command

NAME = "cursor"


def _input(payload):
    data = payload.get("tool_input")
    return data if isinstance(data, dict) else payload


def edits(payload):
    return []


def pull_request(payload):
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


def followup(message):
    print(json.dumps({
        "followup_message": message,
    }))
    return 0
