"""Optional AI check: does the commit / PR description match the diff?

Mechanical rules catch structure. This catches truth — a filled-in PR about
the wrong change, a commit message that says "fix typo" over a schema
migration. It is off by default, warn-only when enabled, and fails open on
timeouts, missing keys, or unreadable replies — the same philosophy as a
broken config never wedging a commit.

Providers:

  session  Hand the criterion back to the agent that is already running
           (hook path). Costs nothing. Useless for a bare `git commit`.
  api      Call an OpenAI-compatible chat endpoint. Needed for pre-commit
           and CI. Requires an API key in the configured env var.
"""

import hashlib
import json
import os
import re
import urllib.error
import urllib.request

from . import gitinfo

CACHE_DIR = os.path.join(".sanity", "cache", "judge")

COMMIT_PROMPT = """\
You are checking whether a git commit message matches the staged diff.

Reply with JSON only, no markdown fences:
{{"pass": true|false, "reason": "one short sentence"}}

pass=true if the message is an honest, specific summary of the change.
pass=false if it is vague, wrong, or describes a different change.

Commit message:
---
{message}
---

Staged diff summary:
---
{diff}
---
"""

PR_PROMPT = """\
You are checking whether a pull request description matches the diff.

Reply with JSON only, no markdown fences:
{{"pass": true|false, "reason": "one short sentence"}}

pass=true if the description honestly explains what changed and why.
pass=false if it is boilerplate, wrong about the diff, or empty of substance.

Title: {title}

Body:
---
{body}
---

Diff summary:
---
{diff}
---
"""

SESSION_HINT = """\
sanity judge ({kind}): review the following against the repository's rules
and the actual change. If it misrepresents the diff, rewrite it before
continuing.

{payload}
"""


def settings(config):
    section = (config or {}).get("judge") or {}
    return {
        "enabled": bool(section.get("enabled")),
        "mode": str(section.get("mode") or "warn").lower(),
        "when": list(section.get("when") or ["commit", "pr"]),
        "provider": str(section.get("provider") or "session").lower(),
        "api": dict(section.get("api") or {}),
        "max_diff_chars": int(section.get("max_diff_chars") or 12000),
        "cache": section.get("cache", True),
    }


def active(config, kind):
    opts = settings(config)
    return (
        opts["enabled"]
        and opts["mode"] != "off"
        and kind in opts["when"]
    )


def diff_summary(max_chars=12000, root=None):
    """A bounded summary of the staged change for the judge prompt."""
    cwd = root or os.getcwd()
    stat = gitinfo.run_in(cwd, ["git", "diff", "--cached", "--stat"]) or ""
    names = gitinfo.run_in(
        cwd, ["git", "diff", "--cached", "--name-status"]
    ) or ""
    patch = gitinfo.run_in(
        cwd, ["git", "diff", "--cached", "--no-color", "-U1"]
    ) or ""
    body = "\n".join(part for part in (stat, names, patch) if part).strip()
    if len(body) > max_chars:
        body = body[:max_chars] + "\n… [truncated by sanity judge]"
    return body or "(no staged changes)"


def _parse_verdict(text):
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    if "pass" not in data:
        return None
    return bool(data["pass"]), str(data.get("reason") or "").strip()


def _cache_path(root, key):
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
    return os.path.join(root, CACHE_DIR, digest + ".json")


def _cache_get(root, key, enabled):
    if not enabled:
        return None
    path = _cache_path(root, key)
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data.get("pass"), data.get("reason", "")
    except Exception:
        return None


def _cache_put(root, key, passed, reason, enabled):
    if not enabled:
        return
    path = _cache_path(root, key)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"pass": passed, "reason": reason}, handle)
    except OSError:
        pass


def _api_chat(api, prompt):
    """Call an OpenAI-compatible /chat/completions endpoint. None on failure."""
    key_env = api.get("api_key_env") or "OPENAI_API_KEY"
    api_key = os.environ.get(key_env) or api.get("api_key")
    if not api_key:
        return None, "no API key in $%s" % key_env

    base = (api.get("base_url") or "https://api.openai.com/v1").rstrip("/")
    model = api.get("model") or "gpt-4o-mini"
    timeout = float(api.get("timeout") or 30)
    payload = json.dumps({
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system",
             "content": "You judge commit and PR descriptions. JSON only."},
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
    }).encode("utf-8")

    request = urllib.request.Request(
        base + "/chat/completions",
        data=payload,
        headers={
            "Authorization": "Bearer %s" % api_key,
            "Content-Type": "application/json",
            "User-Agent": "sanity-hooks",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            ValueError, OSError) as error:
        return None, "api call failed: %s" % error

    try:
        text = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None, "unreadable api response"
    return text, None


def session_message(kind, **fields):
    """Text to inject into an agent turn. Never blocks by itself."""
    if kind == "commit":
        payload = "Commit message:\n%s\n\nDiff:\n%s" % (
            fields.get("message") or "", fields.get("diff") or "",
        )
    else:
        payload = "Title: %s\n\nBody:\n%s\n\nDiff:\n%s" % (
            fields.get("title") or "",
            fields.get("body") or "",
            fields.get("diff") or "",
        )
    return SESSION_HINT.format(kind=kind, payload=payload)


def evaluate(config, kind, root=None, message=None, title=None, body=None,
             diff=None):
    """(passed, reason, channel).

    channel is "ok", "skip", "session", "warn", or "error".
    passed is True when the change is fine or when we fail open.
    """
    opts = settings(config)
    if not active(config, kind):
        return True, "", "skip"

    root = root or os.getcwd()
    diff = diff if diff is not None else diff_summary(
        opts["max_diff_chars"], root=root
    )

    if opts["provider"] == "session":
        return True, session_message(
            kind, message=message, title=title, body=body, diff=diff
        ), "session"

    if kind == "commit":
        prompt = COMMIT_PROMPT.format(message=message or "", diff=diff)
        cache_key = "commit\0%s\0%s" % (message or "", diff)
    else:
        prompt = PR_PROMPT.format(
            title=title or "", body=body or "", diff=diff
        )
        cache_key = "pr\0%s\0%s\0%s" % (title or "", body or "", diff)

    cached = _cache_get(root, cache_key, opts["cache"])
    if cached is not None:
        passed, reason = cached
        return passed, reason, "ok"

    text, error = _api_chat(opts["api"], prompt)
    if error:
        return True, error, "error"

    parsed = _parse_verdict(text)
    if parsed is None:
        return True, "unparseable judge reply", "error"

    passed, reason = parsed
    _cache_put(root, cache_key, passed, reason, opts["cache"])
    return passed, reason, "ok"


def report(kind, passed, reason, channel, mode="warn"):
    if channel == "skip":
        return ""
    if channel == "session":
        return reason
    if channel == "error":
        return "sanity judge (%s): skipped — %s" % (kind, reason)
    if passed:
        return ""
    label = "warning" if mode == "warn" else "blocked"
    return "sanity judge (%s) %s: %s" % (kind, label, reason or "no reason")
