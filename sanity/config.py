"""Config resolution for sanity.

Later sources win:

  1. DEFAULTS below
  2. ~/.claude/sanity.json  or  ~/.sanity.json      (personal baseline)
  3. <repo>/.sanity/config.json  or  <repo>/.sanity.json    (committed)
  4. SANITY_MODE / SANITY_COMMENTS_MODE / SANITY_PR_MODE / SANITY_JUDGE_*

A repo grows into `.sanity/`: a folder holds the config next to one file per
rule (see rules.py). A single `.sanity.json` keeps working and means the same
thing, so nothing has to move until there are rules worth splitting up.

Dicts merge key by key; lists replace, except `extra_*` keys, which append.
Anything unreadable is ignored — a broken config must never wedge a commit.
"""

import copy
import json
import os
import re

DEFAULTS = {
    "comments": {
        "mode": "block",
        "ignore_paths": [
            "**/*.min.js", "**/vendor/**", "**/node_modules/**",
            "**/*.generated.*", "**/*_pb2.py", "**/migrations/**",
            "**/dist/**", "**/build/**",
        ],
        "rules": {
            "restatement": True,
            "banner": True,
            "boilerplate": True,
            "narration": True,
            "generic_verb": True,
        },
        "restatement": {"coverage": 0.5, "max_words": 10, "verb_max_words": 6},
        "extra_allow_prefixes": [],
        "extra_allow_patterns": [],
        "extra_deny_patterns": [],
        "extra_extensions": {},
        "disable_extensions": [],
    },
    "rules": {"mode": "block"},
    "judge": {
        "enabled": False,
        "mode": "warn",
        "when": ["commit", "pr"],
        "provider": "session",
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
    "pull_request": {
        "mode": "block",
        "template": "auto",
        "skip_if_no_template": True,
        "skip_draft": False,
        "require_sections": "from_template",
        "section_match": "exact",
        "require_non_empty_sections": True,
        "min_section_words": 3,
        "require_checklist_items": "present",
        "forbid_template_comments": True,
        "forbid_any_html_comment": False,
        "extra_forbidden_patterns": [],
        "min_body_words": 0,
        "require_ticket": {
            "enabled": False,
            "pattern": r"\b([A-Z][A-Z0-9]+-\d+|#\d+)\b",
            "search": ["title", "body"],
            "message": "Reference the ticket, e.g. ENG-123 or #456.",
        },
        "title": {
            "enabled": False,
            "pattern": r"^(feat|fix|chore|docs|refactor|perf|test|build|ci)"
                       r"(\(.+\))?!?: .{10,}",
            "message": "Use Conventional Commits, "
                       "e.g. 'fix(api): reject empty cursor'.",
        },
        "template_paths": [
            ".github/pull_request_template.md",
            ".github/PULL_REQUEST_TEMPLATE.md",
            ".github/PULL_REQUEST_TEMPLATE/*.md",
            "docs/pull_request_template.md",
            "pull_request_template.md",
            "PULL_REQUEST_TEMPLATE.md",
            ".gitlab/merge_request_templates/Default.md",
            ".gitlab/merge_request_templates/*.md",
        ],
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

    every = os.environ.get("SANITY_MODE")
    if every:
        config["comments"]["mode"] = every.lower()
        config["pull_request"]["mode"] = every.lower()
        config["rules"]["mode"] = every.lower()
    if os.environ.get("SANITY_COMMENTS_MODE"):
        config["comments"]["mode"] = os.environ["SANITY_COMMENTS_MODE"].lower()
    if os.environ.get("SANITY_PR_MODE"):
        config["pull_request"]["mode"] = os.environ["SANITY_PR_MODE"].lower()
    if os.environ.get("SANITY_RULES_MODE"):
        config["rules"]["mode"] = os.environ["SANITY_RULES_MODE"].lower()
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
    """A glob where `**` spans directories and `*` stops at one.

    `fnmatch` lets a single `*` cross `/`, which makes `src/**/*.ts` miss
    `src/a.ts` — the pattern everyone writes first, and the one they mean to
    include the top level.
    """
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
    """True if `path` matches any of `patterns`.

    The full path, the path relative to the repository root, and the bare
    filename are all tried, because agents report absolute paths while rules
    are written relative to the repository.
    """
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


def compiled(patterns, flags=0):
    out = []
    for pattern in patterns or []:
        try:
            out.append(re.compile(pattern, flags))
        except re.error:
            continue
    return out


def compiled_rules(entries, flags=0):
    """[{"pattern","reason"}] -> [(regex, reason)], skipping bad entries."""
    out = []
    for entry in entries or []:
        if not isinstance(entry, dict) or "pattern" not in entry:
            continue
        try:
            out.append((
                re.compile(entry["pattern"], flags),
                entry.get("reason", "matches a disallowed pattern"),
            ))
        except re.error:
            continue
    return out
