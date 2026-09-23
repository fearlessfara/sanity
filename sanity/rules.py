"""Repository rules, one per file under `.sanity/rules/`.

A rule file is Markdown holding a single fenced ```sanity block. The prose
around it is for the model (and, for `check: "judge"`, is the grading
criterion). The block says how sanity verifies compliance:

  deny / require  — deterministic regex (cheap whip)
  check: "judge"  — natural-language rule; an LLM grades the change

Anything with no check at all belongs in `.sanity/guidance/` — compiled into
instruction files, never verified.
"""

import glob
import json
import os
import re

from . import config as config_module, skip as skip_module

BLOCK = re.compile(r"^```sanity\s*\n(.*?)\n```\s*$", re.M | re.S)
MODES = ("block", "warn", "off")
SURFACES = ("files", "pull_request", "change")
CHECKS = ("deny", "require", "judge")
RULES_DIR = os.path.join(".sanity", "rules")
GUIDANCE_DIR = os.path.join(".sanity", "guidance")


class Rule:
    def __init__(self, identifier, spec, source, prose=""):
        self.id = identifier
        self.source = source
        self.prose = prose
        self.severity = str(spec.get("severity", "block")).lower()
        self.surface = str(spec.get("surface", "files")).lower()
        self.paths = spec.get("paths") or []
        self.message = spec.get("message") or ""
        self.check = str(spec.get("check") or "").lower()
        flags = re.I if spec.get("ignore_case") else 0
        self.deny = re.compile(spec["deny"], flags) if spec.get("deny") else None
        self.require = (
            re.compile(spec["require"], flags) if spec.get("require") else None
        )
        # Explicit criterion, else the markdown body (title + prose).
        self.criterion = (spec.get("criterion") or "").strip() or None
        if self.deny is not None:
            self.check = "deny"
        elif self.require is not None:
            self.check = "require"
        elif not self.check:
            self.check = "judge"

    @property
    def is_judge(self):
        return self.check == "judge"

    def applies_to(self, path, root=None):
        if not self.paths:
            return True
        return config_module.path_matches(path, self.paths, root)

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
        """Natural-language standard the judge grades against."""
        if self.criterion:
            return self.criterion
        parts = [self.title()]
        body = self.body()
        if body:
            parts.append(body)
        return "\n\n".join(parts)

    def describe(self):
        if self.is_judge:
            where = {
                "pull_request": "PR/MR descriptions and the change",
                "change": "the staged change",
                "files": "the staged change",
            }.get(self.surface, "the change")
            return "%s — judged by AI against the rule prose" % where
        if self.surface == "pull_request":
            where = "PR and MR descriptions"
        elif self.paths:
            where = ", ".join("`%s`" % pattern for pattern in self.paths)
        else:
            where = "every file"
        what = (
            "must not match `%s`" % self.deny.pattern if self.deny is not None
            else "must contain `%s`" % self.require.pattern
        )
        return "%s %s" % (where, what)


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
    has_deny = bool(spec.get("deny"))
    has_require = bool(spec.get("require"))
    check = str(spec.get("check") or "").lower()

    if check == "judge":
        if has_deny or has_require:
            return None, "%s: judge rules cannot also set deny/require" % identifier
        spec = dict(spec)
        spec["check"] = "judge"
    elif has_deny or has_require:
        if has_deny == has_require:
            return None, (
                "%s: give exactly one of deny, require, or check:\"judge\""
                % identifier
            )
        if check and check not in ("deny", "require"):
            return None, "%s: unknown check %r" % (identifier, check)
    else:
        return None, (
            "%s: give exactly one of deny, require, or check:\"judge\""
            % identifier
        )

    if str(spec.get("severity", "block")).lower() not in MODES:
        return None, "%s: severity must be one of %s" % (identifier, ", ".join(MODES))
    if str(spec.get("surface", "files")).lower() not in SURFACES:
        return None, "%s: surface must be one of %s" % (
            identifier, ", ".join(SURFACES)
        )

    try:
        rule = Rule(identifier, spec, source, BLOCK.sub("", text).strip())
    except re.error as error:
        return None, "%s: bad regex: %s" % (identifier, error)
    if rule.is_judge and not rule.grading_criterion().strip():
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

    def mechanical(self):
        return [rule for rule in self.rules if not rule.is_judge]

    def judged(self):
        return [rule for rule in self.rules if rule.is_judge]

    def _for(self, surface, mechanical_only=True):
        out = []
        for rule in self.rules:
            if mechanical_only and rule.is_judge:
                continue
            if not mechanical_only and not rule.is_judge:
                continue
            if surface == "change" and rule.surface in ("files", "change"):
                out.append(rule)
            elif rule.surface == surface:
                out.append(rule)
            elif surface == "files" and rule.surface == "change":
                out.append(rule)
        return out

    def scan(self, path, lines, added=None):
        """Mechanical file rules only. [(line, text, reason, severity)]."""
        applicable = [
            rule for rule in self._for("files", mechanical_only=True)
            if rule.applies_to(path, self.root)
        ]
        if not applicable:
            return []
        if added is None:
            added = set(range(len(lines)))

        skips = skip_module.collect(lines)
        found = []
        for rule in applicable:
            if skips.covers(rule.id):
                continue
            if rule.require is not None:
                if not rule.require.search("\n".join(lines)):
                    found.append((
                        1, lines[0] if lines else "",
                        "%s (required pattern missing)" % rule.reason(),
                        rule.severity,
                    ))
                continue
            for index in sorted(added):
                if index >= len(lines):
                    continue
                if skips.covers(rule.id, index):
                    continue
                if rule.deny.search(lines[index]):
                    found.append(
                        (index + 1, lines[index].strip(), rule.reason(),
                         rule.severity)
                    )
        return found

    def check_text(self, text, surface="pull_request"):
        """Mechanical PR rules only. [(label, detail, severity)]."""
        skips = skip_module.collect_text(text)
        found = []
        for rule in self._for(surface, mechanical_only=True):
            if skips.covers(rule.id):
                continue
            if rule.require is not None:
                if not rule.require.search(text or ""):
                    found.append(
                        (rule.id, rule.message or "required pattern missing",
                         rule.severity)
                    )
            else:
                # Line-level skips: only suppress if every match is skipped.
                hits = list(rule.deny.finditer(text or ""))
                if not hits:
                    continue
                surviving = False
                detail = rule.message or ""
                for match in hits:
                    before = (text or "")[:match.start()]
                    index = before.count("\n")
                    if not skips.covers(rule.id, index):
                        surviving = True
                        detail = rule.message or match.group(0)[:70]
                        break
                if surviving:
                    found.append((rule.id, detail, rule.severity))
        return found


def report(path, violations):
    blocking = any(severity == "block" for *_, severity in violations)
    headline = (
        "sanity blocked this edit — rule violation(s) (%d):"
        if blocking else
        "sanity warning — rule violation(s) (%d):"
    ) % len(violations)
    lines = [headline, ""]
    for lineno, text, reason, severity in violations:
        label = "%s:%d" % (path, lineno)
        lines.append("  %-28s %s" % (label, text[:60]))
        lines.append("      -> %s%s" % (
            reason, "" if severity == "block" else " [%s]" % severity
        ))
    lines += [
        "",
        "Fix the violation, then retry the edit. Do not rephrase the same "
        "break to dodge the check.",
    ]
    return "\n".join(lines)


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
