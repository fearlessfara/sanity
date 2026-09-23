"""Natural-language and description judges.

Two layers:

1. Built-in match (`judge.enabled`) — does the commit/PR text match the diff?
2. Per-rule `check: "judge"` — does the change respect *your* NL rule prose?

Both use the same providers:

  session  Inject the criterion into the live agent turn (fast whip, free).
  api      OpenAI-compatible chat call (pre-commit / CI).
  auto     session when an agent adapter is present, else api.

Fail open on missing keys, timeouts, or unreadable replies — a flaky model
must never wedge a commit unless the user asked for block mode *and* got a
real verdict.
"""

import hashlib
import json
import os
import re
import urllib.error
import urllib.request

from . import gitinfo, rules as rules_module, skip as skip_module

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

RULE_PROMPT = """\
You are verifying whether a code change respects a repository rule written
in natural language. Be strict: if the change clearly violates the rule,
fail it. If the change is unrelated to the rule, pass it.

Reply with JSON only, no markdown fences:
{{"pass": true|false, "reason": "one short sentence"}}

Rule id: {rule_id}

Rule (the standard to grade against):
---
{criterion}
---

Change under review:
---
{context}
---
"""

SESSION_HINT = """\
sanity judge ({kind}): review the following against the repository's rules
and the actual change. If it misrepresents the diff, rewrite it before
continuing.

{payload}
"""

SESSION_RULES = """\
sanity: review your change against these natural-language rules before
stopping. If any rule is violated, fix the change now. Do not argue with
the criterion — rewrite the work. If every rule is already satisfied,
stop.

{rules}

Change under review:
---
{context}
---
"""


def settings(config):
    section = (config or {}).get("judge") or {}
    return {
        "enabled": bool(section.get("enabled")),
        "mode": str(section.get("mode") or "warn").lower(),
        "when": list(section.get("when") or ["commit", "pr"]),
        "provider": str(section.get("provider") or "auto").lower(),
        "api": dict(section.get("api") or {}),
        "max_diff_chars": int(section.get("max_diff_chars") or 12000),
        "cache": section.get("cache", True),
    }


def resolve_provider(config, for_agent=False):
    provider = settings(config)["provider"]
    if provider == "auto":
        return "session" if for_agent else "api"
    return provider


def active(config, kind):
    opts = settings(config)
    return (
        opts["enabled"]
        and opts["mode"] != "off"
        and kind in opts["when"]
    )


def diff_summary(max_chars=12000, root=None, worktree=False):
    """A bounded summary of the change for the judge prompt.

    `worktree=False` (default): staged only — right for pre-commit / commit-msg.
    `worktree=True`: `git diff HEAD` — staged + unstaged, for agent stop hooks
    where the model often has not staged yet.
    """
    cwd = root or os.getcwd()
    if worktree:
        args_stat = ["git", "diff", "HEAD", "--stat"]
        args_names = ["git", "diff", "HEAD", "--name-status"]
        args_patch = ["git", "diff", "HEAD", "--no-color", "-U1"]
        empty = "(no working-tree changes)"
    else:
        args_stat = ["git", "diff", "--cached", "--stat"]
        args_names = ["git", "diff", "--cached", "--name-status"]
        args_patch = ["git", "diff", "--cached", "--no-color", "-U1"]
        empty = "(no staged changes)"
    stat = gitinfo.run_in(cwd, args_stat) or ""
    names = gitinfo.run_in(cwd, args_names) or ""
    patch = gitinfo.run_in(cwd, args_patch) or ""
    body = "\n".join(part for part in (stat, names, patch) if part).strip()
    if len(body) > max_chars:
        body = body[:max_chars] + "\n… [truncated by sanity judge]"
    return body or empty


def _context(opts, root, title=None, body=None, message=None, diff=None,
             worktree=False):
    diff = diff if diff is not None else diff_summary(
        opts["max_diff_chars"], root=root, worktree=worktree
    )
    parts = []
    if title or body:
        parts.append("PR title: %s" % (title or ""))
        parts.append("PR body:\n%s" % (body or ""))
    if message:
        parts.append("Commit message:\n%s" % message)
    parts.append("Diff:\n%s" % diff)
    return "\n\n".join(parts), diff


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


def _api_chat(api, prompt, system=None):
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
            {"role": "system", "content": system or (
                "You enforce repository rules. Reply with JSON only."
            )},
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
             diff=None, for_agent=False):
    """Built-in commit/PR match. (passed, reason, channel)."""
    opts = settings(config)
    if not active(config, kind):
        return True, "", "skip"

    root = root or os.getcwd()
    context, diff = _context(
        opts, root, title=title, body=body, message=message, diff=diff
    )
    provider = resolve_provider(config, for_agent=for_agent)

    if provider == "session":
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

    if not _api_key_present(opts["api"]):
        return True, _no_key_message(opts["api"]), "error"

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


def _select_rules(ruleset, surface):
    judged = list(ruleset.judged()) if ruleset else []
    if surface == "pull_request":
        return [r for r in judged if r.surface == "pull_request"]
    # change / commit / files / stop: grade change-scoped rules
    return [
        r for r in judged
        if r.surface in ("files", "change", "pull_request")
        or (surface == "change" and r.surface == "files")
    ]


def _api_key_present(api):
    key_env = (api or {}).get("api_key_env") or "OPENAI_API_KEY"
    return bool(os.environ.get(key_env) or (api or {}).get("api_key"))


def _no_key_message(api):
    return "no API key in $%s" % ((api or {}).get("api_key_env") or "OPENAI_API_KEY")


def resolve_rules_provider(config, for_agent=False):
    """Provider for check:\"judge\" rules.

    `auto` prefers a real API grade when a key is present (strong stop loop),
    otherwise falls back to a one-shot session self-review inside an agent.
    """
    provider = settings(config)["provider"]
    if provider != "auto":
        return provider
    if _api_key_present(settings(config)["api"]):
        return "api"
    return "session" if for_agent else "api"


def evaluate_rules(config, ruleset, surface="change", root=None,
                   title=None, body=None, message=None, diff=None,
                   for_agent=False, worktree=False):
    """Grade every check:\"judge\" rule.

    Returns (results, advice_or_report, blocking).

    results: [(rule, passed, reason, channel)]
    """
    opts = settings(config)
    root = root or os.getcwd()
    selected = _select_rules(ruleset, surface)
    if not selected:
        return [], "", False

    # File-level skips in the PR body suppress named judge rules.
    body_skips = skip_module.collect_text(body or "")
    selected = [r for r in selected if not body_skips.covers(r.id)]
    if not selected:
        return [], "", False

    context, diff = _context(
        opts, root, title=title, body=body, message=message, diff=diff,
        worktree=worktree,
    )
    # Nothing to grade — do not poke the agent into another turn.
    if diff.startswith("(no ") and not (title or body or message):
        return [], "", False

    provider = resolve_rules_provider(config, for_agent=for_agent)

    if provider == "session":
        blocks = []
        for rule in selected:
            blocks.append("### %s\n%s" % (rule.id, rule.grading_criterion()))
        advice = SESSION_RULES.format(
            rules="\n\n".join(blocks), context=context
        )
        results = [(rule, True, "", "session") for rule in selected]
        # Session cannot hard-fail; the agent is told to self-review once.
        return results, advice, False

    # A missing key must fail open right now, not replay a verdict an
    # earlier run cached back when a key was configured.
    key_present = _api_key_present(opts["api"])

    results = []
    blocking = False
    for rule in selected:
        cache_key = "rule\0%s\0%s\0%s" % (
            rule.id, rule.grading_criterion(), context
        )
        if not key_present:
            passed, reason, channel = True, _no_key_message(opts["api"]), "error"
        else:
            cached = _cache_get(root, cache_key, opts["cache"])
            if cached is not None:
                passed, reason = cached
                channel = "ok"
            else:
                prompt = RULE_PROMPT.format(
                    rule_id=rule.id,
                    criterion=rule.grading_criterion(),
                    context=context,
                )
                text, error = _api_chat(opts["api"], prompt)
                if error:
                    passed, reason, channel = True, error, "error"
                else:
                    parsed = _parse_verdict(text)
                    if parsed is None:
                        passed, reason, channel = (
                            True, "unparseable judge reply", "error"
                        )
                    else:
                        passed, reason = parsed
                        channel = "ok"
                        _cache_put(
                            root, cache_key, passed, reason, opts["cache"]
                        )

        results.append((rule, passed, reason, channel))
        if not passed and channel == "ok" and rule.severity == "block":
            blocking = True

    report = rules_module.report_judge([
        (rule, passed, reason, channel, rule.severity)
        for rule, passed, reason, channel in results
    ])
    # Also surface soft errors so operators see fail-open skips.
    errors = [
        "sanity judge (%s): skipped — %s" % (rule.id, reason)
        for rule, passed, reason, channel in results
        if channel == "error"
    ]
    if errors and not report:
        report = "\n".join(errors)
    elif errors:
        report = report + "\n\n" + "\n".join(errors)

    return results, report, blocking


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
