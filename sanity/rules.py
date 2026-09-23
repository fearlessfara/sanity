"""Repository rules, one per file under `.sanity/rules/`.

A rule file is Markdown holding a single fenced ```sanity block. The prose
around it is written for the model — it is what explains the rule in
`CLAUDE.md`, `AGENTS.md` and `.cursor/rules` — and the block is the part this
module enforces:

    # No console.log in shipped code

    Use the `logger` module instead, so output stays structured and can be
    filtered in production.

    ```sanity
    { "paths": ["src/**/*.ts"], "deny": "\\bconsole\\.log\\s*\\(",
      "message": "use logger.debug() instead" }
    ```

Splitting it this way is the whole point of the folder. Anything that cannot
be stated as a `deny` or a `require` belongs in `.sanity/guidance/`, which is
compiled into the instruction files and never blocks anything — a rule an
agent can only be *asked* to follow should not pretend to be mechanical.

A rule that fails to parse is skipped with a warning rather than wedging a
commit. `--strict`, which CI runs, turns those warnings into failures, so a
corrupt rule cannot silently stop enforcing.
"""

import glob
import json
import os
import re

from . import config as config_module

BLOCK = re.compile(r"^```sanity\s*\n(.*?)\n```\s*$", re.M | re.S)
MODES = ("block", "warn", "off")
SURFACES = ("files", "pull_request")
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
        flags = re.I if spec.get("ignore_case") else 0
        self.deny = re.compile(spec["deny"], flags) if spec.get("deny") else None
        self.require = (
            re.compile(spec["require"], flags) if spec.get("require") else None
        )

    def applies_to(self, path, root=None):
        if not self.paths:
            return True
        return config_module.path_matches(path, self.paths, root)

    def reason(self):
        return "%s%s" % (self.id, " — " + self.message if self.message else "")

    def title(self):
        """The rule's first Markdown heading, else its id."""
        for line in self.prose.splitlines():
            if line.startswith("#"):
                return line.lstrip("#").strip()
        return self.id

    def body(self):
        """The prose with the leading heading removed."""
        lines = self.prose.splitlines()
        if lines and lines[0].startswith("#"):
            lines = lines[1:]
        return "\n".join(lines).strip()

    def describe(self):
        """One line telling a model exactly what is mechanically checked."""
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
    if bool(spec.get("deny")) == bool(spec.get("require")):
        return None, "%s: give exactly one of deny or require" % identifier
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
    """[(title, body)] from `.sanity/guidance/*.md`.

    Guidance is prose that cannot be mechanically checked. It is compiled
    into the instruction files and never blocks anything.
    """
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

    def _for(self, surface):
        return [rule for rule in self.rules if rule.surface == surface]

    def scan(self, path, lines, added=None):
        """[(line_number, source_line, reason, severity)] for a file."""
        applicable = [
            rule for rule in self._for("files") if rule.applies_to(path, self.root)
        ]
        if not applicable:
            return []
        if added is None:
            added = set(range(len(lines)))

        found = []
        for rule in applicable:
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
                if rule.deny.search(lines[index]):
                    found.append(
                        (index + 1, lines[index].strip(), rule.reason(),
                         rule.severity)
                    )
        return found

    def check_text(self, text, surface="pull_request"):
        """[(label, detail, severity)] for a PR body or similar."""
        found = []
        for rule in self._for(surface):
            if rule.require is not None:
                if not rule.require.search(text or ""):
                    found.append(
                        (rule.id, rule.message or "required pattern missing",
                         rule.severity)
                    )
            else:
                match = rule.deny.search(text or "")
                if match:
                    found.append(
                        (rule.id, rule.message or match.group(0)[:70],
                         rule.severity)
                    )
        return found


def report(path, violations):
    lines = ["%s: %d rule violation(s)." % (path, len(violations)), ""]
    for lineno, text, reason, severity in violations:
        label = "%s:%d" % (path, lineno)
        lines.append("  %-28s %s" % (label, text[:60]))
        lines.append("      -> %s%s" % (
            reason, "" if severity == "block" else " [%s]" % severity
        ))
    return "\n".join(lines)
