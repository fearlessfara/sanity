"""Which agent is on the other end of the pipe.

Claude Code, Cursor and Codex all run command hooks the same way — one JSON
object on stdin, exit 2 to block — and all three disagree about field names,
tool names, and the shape of a structured refusal. Each adapter exposes the
same three functions, so the checks never learn which agent they serve:

    edits(payload)         -> [(path, new_lines, added_indices)]
    pull_request(payload)  -> (kind, title, body, flags) or None
    refuse(message)        -> exit code, having written the refusal

The generated manifests pass `--agent` explicitly. Detection only matters for
hand-rolled setups, and falls back to Claude, which is what shipped first.
"""

from . import claude, codex, cursor

ADAPTERS = {module.NAME: module for module in (claude, cursor, codex)}
NAMES = ("auto",) + tuple(sorted(ADAPTERS))

CURSOR_KEYS = ("conversation_id", "generation_id", "workspace_roots")
CODEX_KEYS = ("turn_id", "tool_use_id")


def detect(payload):
    event = payload.get("hook_event_name") or payload.get("hookEventName") or ""
    if any(key in payload for key in CURSOR_KEYS):
        return cursor
    if event[:1].islower() and event:
        return cursor
    if any(key in payload for key in CODEX_KEYS):
        return codex
    if payload.get("tool_name") == "apply_patch":
        return codex
    return claude


def get(name, payload=None):
    if name and name != "auto":
        return ADAPTERS[name]
    return detect(payload or {})
