"""Adapter for Codex CLI hook payloads (JSON on stdin)."""

import json
import re
import sys

from .pr import parse_command

NAME = "codex"


def edits(payload):
    return []


def pull_request(payload):
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


def followup(message):
    print(json.dumps({
        "decision": "block",
        "reason": message,
    }))
    return 0
