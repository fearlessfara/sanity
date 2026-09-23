"""Config resolution for sanity.

Later sources win:

  1. DEFAULTS below
  2. ~/.claude/sanity.json  or  ~/.sanity.json
  3. <repo>/.sanity/config.json  or  <repo>/.sanity.json
  4. SANITY_MODE / SANITY_JUDGE_*
"""

import copy
import json
import os
import re

DEFAULTS = {
    "judge": {
        "enabled": False,
        "mode": "warn",
        "when": ["commit", "pr"],
        "provider": "auto",
        "api": {
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4o-mini",
            "api_key_env": "OPENAI_API_KEY",
            "timeout": 30,
        },
        "max_diff_chars": 12000,
        "cache": True,
    },
    "sync": {
        "targets": {
            "claude": "CLAUDE.md",
            "codex": "AGENTS.md",
            "cursor": ".cursor/rules/sanity.mdc",
        },
    },
}

PERSONAL = (
    os.path.join("~", ".claude", "sanity.json"),
    os.path.join("~", ".sanity.json"),
)
PROJECT = (
    os.path.join(".sanity", "config.json"),
    ".sanity.json",
    os.path.join(".claude", "sanity.json"),
    "sanity.json",
)


def _merge(base, over):
    out = copy.deepcopy(base)
    for key, value in (over or {}).items():
        if key.startswith("extra_") and isinstance(value, list):
            out[key] = list(out.get(key) or []) + value
        elif isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _read(path):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def repo_root(start=None):
    """Nearest ancestor holding .git, else the starting directory."""
    start = os.path.abspath(
        start or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    )
    current = start
    while True:
        if os.path.exists(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return start
        current = parent


def load(start=None):
    """Return (config, sources) — sources being the files that contributed."""
    config = copy.deepcopy(DEFAULTS)
    sources = []

    for name in PERSONAL:
        path = os.path.expanduser(name)
        data = _read(path)
        if data:
            config = _merge(config, data)
            sources.append(path)
            break

    root = repo_root(start)
    for name in PROJECT:
        path = os.path.join(root, name)
        data = _read(path)
        if data:
            config = _merge(config, data)
            sources.append(path)
            break

    if os.environ.get("SANITY_MODE"):
        config["judge"]["mode"] = os.environ["SANITY_MODE"].lower()
    if os.environ.get("SANITY_JUDGE_ENABLED"):
        config["judge"]["enabled"] = (
            os.environ["SANITY_JUDGE_ENABLED"].lower() in ("1", "true", "yes")
        )
    if os.environ.get("SANITY_JUDGE_MODE"):
        config["judge"]["mode"] = os.environ["SANITY_JUDGE_MODE"].lower()
    if os.environ.get("SANITY_JUDGE_PROVIDER"):
        config["judge"]["provider"] = os.environ["SANITY_JUDGE_PROVIDER"].lower()

    return config, sources


_GLOB_CACHE = {}


def _glob(pattern):
    """A glob where `**` spans directories and `*` stops at one."""
    if pattern in _GLOB_CACHE:
        return _GLOB_CACHE[pattern]

    out = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            out.append("(?:[^/]*/)*")
            index += 3
        elif pattern.startswith("**", index):
            out.append(".*")
            index += 2
        elif pattern[index] == "*":
            out.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(pattern[index]))
            index += 1

    try:
        regex = re.compile("(?s:%s)\\Z" % "".join(out))
    except re.error:
        regex = re.compile(r"(?!)")
    _GLOB_CACHE[pattern] = regex
    return regex


def path_matches(path, patterns, root=None):
    """True if `path` matches any of `patterns`."""
    if not patterns:
        return False
    candidates = {path.replace(os.sep, "/"), os.path.basename(path)}
    if root and os.path.isabs(path):
        try:
            candidates.add(os.path.relpath(path, root).replace(os.sep, "/"))
        except ValueError:
            pass
    return any(
        _glob(pattern).match(candidate)
        for pattern in patterns
        for candidate in candidates
    )
