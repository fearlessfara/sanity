"""Repository rules — natural-language only.

One Markdown file per rule under `.sanity/rules/`. The prose is the grading
criterion. The fenced ```sanity block says `check: "judge"` and how hard to
push (severity / surface).

Regex / linter concerns belong in eslint, ruff, etc. Sanity exists for the
rules those tools cannot express.
"""

import glob
import json
import os
import re

from . import skip as skip_module

BLOCK = re.compile(r"^```sanity\s*\n(.*?)\n```\s*$", re.M | re.S)
MODES = ("block", "warn", "off")
SURFACES = ("files", "pull_request", "change")
RULES_DIR = os.path.join(".sanity", "rules")
GUIDANCE_DIR = os.path.join(".sanity", "guidance")


class Rule:
    def __init__(self, identifier, spec, source, prose=""):
        self.id = identifier
        self.source = source
        self.prose = prose
        self.severity = str(spec.get("severity", "warn")).lower()
        self.surface = str(spec.get("surface", "change")).lower()
        self.paths = spec.get("paths") or []
        self.message = spec.get("message") or ""
        self.check = "judge"
        self.criterion = (spec.get("criterion") or "").strip() or None

    @property
    def is_judge(self):
        return True

    def reason(self):
        return "%s%s" % (self.id, " — " + self.message if self.message else "")

    def title(self):
        for line in self.prose.splitlines():
            if line.startswith("#"):
                return line.lstrip("#").strip()
        return self.id

    def body(self):
        lines = self.prose.splitlines()
        if lines and lines[0].startswith("#"):
            lines = lines[1:]
        return "\n".join(lines).strip()

    def grading_criterion(self):
        if self.criterion:
            return self.criterion
        parts = [self.title()]
        body = self.body()
        if body:
            parts.append(body)
        return "\n\n".join(parts)

    def describe(self):
        where = {
            "pull_request": "PR/MR descriptions and the change",
            "change": "the staged / working-tree change",
            "files": "the change",
        }.get(self.surface, "the change")
        return "%s — judged by AI against the rule prose" % where


def parse(text, source, identifier=None):
    """(Rule, None) or (None, error string)."""
    match = BLOCK.search(text or "")
    if not match:
        return None, "no ```sanity block"
    try:
        spec = json.loads(match.group(1))
    except ValueError as error:
        return None, "unreadable ```sanity block: %s" % error
    if not isinstance(spec, dict):
        return None, "the ```sanity block must be an object"

    identifier = spec.get("id") or identifier or "rule"
    if spec.get("deny") or spec.get("require"):
        return None, (
            "%s: sanity only supports check:\"judge\" — put regex rules in "
            "your linter" % identifier
        )
    check = str(spec.get("check") or "judge").lower()
    if check != "judge":
        return None, (
            "%s: unknown check %r (only \"judge\" is supported)"
            % (identifier, check)
        )

    if str(spec.get("severity", "warn")).lower() not in MODES:
        return None, "%s: severity must be one of %s" % (
            identifier, ", ".join(MODES)
        )
    if str(spec.get("surface", "change")).lower() not in SURFACES:
        return None, "%s: surface must be one of %s" % (
            identifier, ", ".join(SURFACES)
        )

    prose = BLOCK.sub("", text).strip()
    rule = Rule(identifier, spec, source, prose)
    if not (rule.criterion or rule.prose.strip()):
        return None, "%s: judge rules need prose or a criterion field" % identifier
    return rule, None


def load(root):
    """(rules, errors) from `.sanity/rules/*.md`, sorted by filename."""
    rules, errors = [], []
    for path in sorted(glob.glob(os.path.join(root, RULES_DIR, "*.md"))):
        try:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
        except OSError as error:
            errors.append("%s: %s" % (path, error))
            continue
        stem = os.path.splitext(os.path.basename(path))[0]
        rule, error = parse(text, path, stem)
        if error:
            errors.append("%s: %s" % (path, error))
        elif rule.severity != "off":
            rules.append(rule)
    return rules, errors


def load_guidance(root):
    """[(title, body)] from `.sanity/guidance/*.md` — never enforced."""
    out = []
    pattern = os.path.join(root, GUIDANCE_DIR, "*.md")
    for path in sorted(glob.glob(pattern)):
        try:
            with open(path, encoding="utf-8") as handle:
                text = handle.read().strip()
        except OSError:
            continue
        if not text:
            continue
        lines = text.splitlines()
        if lines[0].startswith("#"):
            out.append((lines[0].lstrip("#").strip(),
                        "\n".join(lines[1:]).strip()))
        else:
            out.append((os.path.splitext(os.path.basename(path))[0], text))
    return out


class RuleSet:
    def __init__(self, rules=(), errors=(), root=None):
        self.rules = list(rules)
        self.errors = list(errors)
        self.root = root

    def __bool__(self):
        return bool(self.rules)

    def judged(self):
        return list(self.rules)

    def mechanical(self):
        return []


def report_judge(results):
    """results: [(rule, passed, reason, channel, severity)]."""
    failing = [row for row in results if not row[1] and row[3] == "ok"]
    if not failing:
        return ""
    blocking = any(row[4] == "block" for row in failing)
    headline = (
        "sanity blocked — natural-language rule(s) not respected (%d):"
        if blocking else
        "sanity warning — natural-language rule(s) may be violated (%d):"
    ) % len(failing)
    lines = [headline, ""]
    for rule, _passed, reason, _channel, severity in failing:
        lines.append("  %-24s %s" % (rule.id + ":", reason or "no reason"))
        if severity != "block":
            lines[-1] += " [warn]"
    lines += [
        "",
        "Fix the change to respect the rule, then retry. Do not argue with "
        "the criterion — rewrite the work.",
    ]
    return "\n".join(lines)
