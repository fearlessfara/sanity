"""Interactive (and flag-driven) natural-language rule authoring.

    sanity new                  walk through prompts, write .sanity/rules/*.md
    sanity new --title '…' -y   non-interactive, for scripts / agents
"""

import json
import os
import re
import sys

from . import rules as rules_module

SLUG = re.compile(r"[^a-z0-9]+")


def slugify(title):
    text = SLUG.sub("-", (title or "").lower()).strip("-")
    return text or "rule"


def build_spec(severity="warn", surface="change", paths=None,
               message=None, criterion=None):
    severity = (severity or "warn").lower()
    if severity not in rules_module.MODES:
        raise ValueError(
            "severity must be one of %s" % ", ".join(rules_module.MODES)
        )
    surface = (surface or "change").lower()
    if surface not in rules_module.SURFACES:
        raise ValueError(
            "surface must be one of %s" % ", ".join(rules_module.SURFACES)
        )

    spec = {
        "check": "judge",
        "severity": severity,
        "surface": surface,
    }
    if message:
        spec["message"] = message
    if criterion:
        spec["criterion"] = criterion
    cleaned = [p.strip() for p in (paths or []) if p and p.strip()]
    if cleaned:
        spec["paths"] = cleaned
    return spec


def render(title, body, spec):
    title = (title or "").strip() or "Untitled rule"
    body = (body or "").strip()
    block = json.dumps(spec, indent=2, sort_keys=False)
    parts = ["# %s" % title, ""]
    if body:
        parts += [body, ""]
    parts += ["```sanity", block, "```", ""]
    return "\n".join(parts)


def target_path(root, identifier):
    directory = os.path.join(root, rules_module.RULES_DIR)
    return os.path.join(directory, "%s.md" % identifier)


def write_rule(root, identifier, title, body, spec, force=False):
    text = render(title, body, spec)
    rule, error = rules_module.parse(text, "new", identifier)
    if error:
        raise ValueError(error)

    path = target_path(root, identifier)
    if os.path.exists(path) and not force:
        raise ValueError(
            "%s already exists (pass --force to overwrite)" % path
        )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path, rule


def _tty():
    return sys.stdin.isatty() and sys.stdout.isatty()


def _ask(prompt, default=None, allow_empty=False):
    suffix = " [%s]" % default if default not in (None, "") else ""
    while True:
        try:
            raw = input("%s%s: " % (prompt, suffix))
        except EOFError:
            print(file=sys.stderr)
            return default if default is not None else ""
        text = raw.strip()
        if not text and default is not None:
            return default
        if text or allow_empty:
            return text
        print("  (needed — press enter to accept a default if one is shown)")


def _ask_choice(prompt, choices, default=None):
    labels = "/".join(
        choice.upper() if choice == default else choice for choice in choices
    )
    while True:
        answer = _ask("%s (%s)" % (prompt, labels), default=default)
        answer = (answer or "").lower()
        if answer in choices:
            return answer
        print("  pick one of: %s" % ", ".join(choices))


def _ask_multiline(prompt):
    print("%s (blank line to finish):" % prompt)
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line == "":
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _ask_list(prompt, default=None):
    raw = _ask(prompt, default=default, allow_empty=True)
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


INTRO = """\
sanity new — write a natural-language rule under .sanity/rules/

The prose you write is what the AI judge grades against. Regex / style
concerns belong in eslint, ruff, and friends — not here.
"""


def interview(defaults=None):
    defaults = defaults or {}
    if not _tty():
        raise ValueError(
            "stdin is not a terminal; pass flags instead "
            "(e.g. sanity new --title '…' --body '…' -y)"
        )

    print(INTRO)

    title = defaults.get("title") or _ask("Title (one line, shown to the model)")
    body = defaults.get("body")
    if body is None:
        body = _ask_multiline(
            "The standard to grade against (why it matters / how to comply)"
        )
    if not body.strip():
        raise ValueError("a judge rule needs prose — that is the criterion")

    surface = defaults.get("surface") or _ask_choice(
        "Where does it apply",
        ["change", "pull_request", "files"],
        default="change",
    )
    paths = defaults.get("paths")
    if paths is None and surface != "pull_request":
        paths = _ask_list(
            "Path globs to limit the grade (comma-separated, empty = all)",
            default="",
        )
    elif paths is None:
        paths = []

    severity = defaults.get("severity") or _ask_choice(
        "Severity",
        ["warn", "block", "off"],
        default="warn",
    )
    message = defaults.get("message")
    if message is None:
        message = _ask(
            "Short message when it fires (optional)",
            default="",
            allow_empty=True,
        )

    identifier = defaults.get("id") or _ask(
        "Filename / id", default=slugify(title)
    )
    identifier = slugify(identifier)

    return {
        "id": identifier,
        "title": title,
        "body": body,
        "surface": surface,
        "paths": paths or [],
        "severity": severity,
        "message": message or None,
    }


def from_args(args):
    title = args.title
    if not title:
        raise ValueError("--title is required in non-interactive mode")
    body = args.body or ""
    if not body.strip() and not args.criterion:
        raise ValueError("--body (or --criterion) is required")
    identifier = slugify(args.id or slugify(title))
    return {
        "id": identifier,
        "title": title,
        "body": body,
        "surface": args.surface or "change",
        "paths": list(args.paths or []),
        "severity": args.severity or "warn",
        "message": args.message,
        "criterion": args.criterion,
    }


def preview(fields, spec):
    text = render(fields["title"], fields.get("body") or "", spec)
    print()
    print("─" * 60)
    print(text.rstrip())
    print("─" * 60)
