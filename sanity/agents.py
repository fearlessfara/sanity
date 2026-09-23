"""Adapter dispatch for Claude / Cursor / Codex hook payloads."""

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
