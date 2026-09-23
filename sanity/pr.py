"""PR/MR descriptions, checked against the repository template."""

import glob
import os
import re
import shlex

from . import config as config_module

HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*\S)\s*$")
BOLD_HEADING = re.compile(r"^\s{0,3}\*\*(.+?)\*\*:?\s*$")
CHECKLIST = re.compile(r"^\s*[-*+]\s*\[( |x|X)\]\s*(.*\S)?\s*$")
HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
# The tag may be followed by the rest of a pipeline before the newline:
#   cat <<'EOF' | gh pr create --body-file -
HEREDOC = re.compile(r"<<-?\s*[\"']?(\w+)[\"']?[^\n]*\n(.*?)\n\s*\1\b", re.S)
PLACEHOLDER = re.compile(
    r"\[(insert|describe|add|your|todo|tbd)[^\]]*\]|<(describe|insert|your)[^>]*>"
    r"|\b(tbd|fixme here)\b",
    re.I,
)

SEPARATORS = {"&&", "||", ";", "|", "&"}
CLI_COMMANDS = [
    (("gh", "pr", "create"), "github"),
    (("gh", "pr", "edit"), "github"),
    (("glab", "mr", "create"), "gitlab"),
    (("glab", "mr", "update"), "gitlab"),
]
TITLE_FLAGS = {"--title", "-t"}
BODY_FLAGS = {"--body", "-b", "--description", "-d"}
BODY_FILE_FLAGS = {"--body-file", "-F"}


# ---------------------------------------------------------------- markdown

def normalise(text):
    text = HTML_COMMENT.sub(" ", text or "")
    text = re.sub(r"[^\w\s]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def headings(text):
    """[(raw_title, normalised_title, body_below)]."""
    lines = (text or "").splitlines()
    marks = []
    for index, line in enumerate(lines):
        match = HEADING.match(line)
        title = match.group(2) if match else None
        if title is None:
            bold = BOLD_HEADING.match(line)
            title = bold.group(1) if bold else None
        if title is not None:
            marks.append((index, title.strip()))

    out = []
    for position, (start, title) in enumerate(marks):
        end = marks[position + 1][0] if position + 1 < len(marks) else len(lines)
        out.append((title, normalise(title), "\n".join(lines[start + 1:end])))
    return out


def checklist_items(text):
    out = []
    for line in (text or "").splitlines():
        match = CHECKLIST.match(line)
        if match and (match.group(2) or "").strip():
            out.append((normalise(match.group(2)), match.group(1).lower() == "x"))
    return out


def section_is_empty(body, min_words):
    stripped = HTML_COMMENT.sub(" ", body or "")
    if any(CHECKLIST.match(line) for line in stripped.splitlines()):
        return False
    words = [w for w in re.findall(r"\S+", stripped) if w not in {"-", "*", "|"}]
    return len(words) < max(1, int(min_words))


# ------------------------------------------------------------ command line

def unwrap_heredoc(value):
    match = HEREDOC.search(value or "")
    return match.group(2) if match else value


def unescape(value):
    r"""A body written on one line with literal \n is still a real body."""
    if value and "\\n" in value and "\n" not in value:
        return value.replace("\\n", "\n").replace('\\"', '"')
    return value


def parse_command(command):
    """(kind, title, body, flags) for a PR-creating command, else None."""
    try:
        parts = shlex.split(command or "", comments=False)
    except ValueError:
        parts = (command or "").split()

    start = kind = None
    for index in range(len(parts)):
        for words, flavour in CLI_COMMANDS:
            if tuple(parts[index:index + len(words)]) == words:
                start, kind = index + len(words), flavour
                break
        if start is not None:
            break
    if start is None:
        return None

    title = body = body_file = None
    flags = set()
    index = start
    while index < len(parts):
        token = parts[index]
        if token in SEPARATORS:
            break
        name, _, inline = token.partition("=")
        value = inline or (parts[index + 1] if index + 1 < len(parts) else None)
        step = 1 if inline else 2

        if name in TITLE_FLAGS:
            title, index = value, index + step
            continue
        if name in BODY_FLAGS:
            body, index = value, index + step
            continue
        if name in BODY_FILE_FLAGS:
            body_file, index = value, index + step
            continue
        if token.startswith("-"):
            flags.add(name.lstrip("-"))
        index += 1

    if body:
        body = unwrap_heredoc(body)
    elif body_file and body_file != "-":
        try:
            with open(body_file, encoding="utf-8") as handle:
                body = handle.read()
        except OSError:
            body = None
    if body is None:
        match = HEREDOC.search(command or "")
        if match:
            body = match.group(2)

    return kind, title, unescape(body), flags


# --------------------------------------------------------------- the check

class PRChecker:
    def __init__(self, config=None, root=None):
        self.config = (config or config_module.DEFAULTS)["pull_request"]
        self.root = root or config_module.repo_root()
        self.mode = (self.config.get("mode") or "block").lower()
        self.template_path = None

    def find_template(self, kind="github"):
        setting = self.config.get("template", "auto")
        if setting is False:
            return None
        if isinstance(setting, str) and setting != "auto":
            path = setting if os.path.isabs(setting) else os.path.join(
                self.root, setting
            )
            return self._read(path) if os.path.exists(path) else None

        patterns = list(self.config.get("template_paths") or [])
        if kind == "gitlab":
            patterns.sort(key=lambda p: ".gitlab" not in p)
        for pattern in patterns:
            for path in sorted(glob.glob(os.path.join(self.root, pattern))):
                if os.path.isfile(path):
                    return self._read(path)
        return None

    def _read(self, path):
        try:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            self.template_path = path
            return text
        except OSError:
            return None

    def check(self, title, body, template, flags=()):
        config = self.config
        problems = []
        body = body or ""

        if not body.strip():
            problems.append((
                "empty description",
                "--fill builds the body from commit messages, skipping the "
                "template" if "fill" in flags else "no description given",
            ))

        required = config.get("require_sections", "from_template")
        if required == "from_template" and template:
            wanted = [(raw, norm) for raw, norm, _ in headings(template)]
        elif isinstance(required, list):
            wanted = [(item, normalise(item)) for item in required]
        else:
            wanted = []

        body_sections = headings(body)
        body_norms = [norm for _, norm, _ in body_sections]
        contains = config.get("section_match", "exact") == "contains"

        for raw, norm in wanted:
            if not norm:
                continue
            hit = norm in body_norms
            if not hit and contains:
                hit = any(norm in c or c in norm for c in body_norms)
            if not hit:
                problems.append(("missing section", "## %s" % raw))

        if config.get("require_non_empty_sections", True):
            min_words = config.get("min_section_words", 3)
            wanted_norms = {norm for _, norm in wanted}
            for raw, norm, section_body in body_sections:
                if wanted_norms and norm not in wanted_norms:
                    continue
                if section_is_empty(section_body, min_words):
                    problems.append(("empty section", "## %s" % raw))

        if template and config.get("forbid_template_comments", True):
            for comment in HTML_COMMENT.findall(template):
                if comment.strip() and comment in body:
                    problems.append((
                        "template comment left in",
                        comment.strip().splitlines()[0][:70],
                    ))
        if config.get("forbid_any_html_comment", False):
            for comment in HTML_COMMENT.findall(body):
                problems.append((
                    "html comment in body",
                    comment.strip().splitlines()[0][:70],
                ))

        for match in PLACEHOLDER.finditer(HTML_COMMENT.sub("", body)):
            problems.append(("unfilled placeholder", match.group(0)[:70]))

        mode = config.get("require_checklist_items", "present")
        if template and mode in ("present", "checked"):
            present = dict(checklist_items(body))
            for text, _ in checklist_items(template):
                if text not in present:
                    problems.append(("checklist item dropped", text[:70]))
                elif mode == "checked" and not present[text]:
                    problems.append(("checklist item unticked", text[:70]))

        minimum = int(config.get("min_body_words", 0) or 0)
        if minimum:
            count = len(re.findall(r"\S+", HTML_COMMENT.sub(" ", body)))
            if count < minimum:
                problems.append((
                    "body too thin", "%d words, minimum %d" % (count, minimum)
                ))

        ticket = config.get("require_ticket") or {}
        if ticket.get("enabled"):
            fields = ticket.get("search") or ["title", "body"]
            haystack = " ".join(
                value for field, value in (("title", title or ""), ("body", body))
                if field in fields
            )
            try:
                if not re.search(ticket.get("pattern", ""), haystack):
                    problems.append(("no ticket reference", ticket.get("message", "")))
            except re.error:
                pass

        rule = config.get("title") or {}
        if rule.get("enabled") and title is not None:
            try:
                if not re.search(rule.get("pattern", ""), title):
                    problems.append(("title convention", rule.get("message", "")))
            except re.error:
                pass

        for regex, reason in config_module.compiled_rules(
            config.get("extra_forbidden_patterns"), re.I
        ):
            match = regex.search(body)
            if match:
                problems.append((reason, match.group(0)[:70]))

        return problems

    def report(self, problems, template, against_template=True):
        headline = (
            "PR description does not match the repository template"
            if against_template
            else "PR description breaks the repository's rules"
        )
        lines = ["%s (%d problem(s))." % (headline, len(problems)), ""]
        for label, detail in problems:
            lines.append("  %-26s %s" % (label + ":", detail))

        if against_template and self.template_path:
            lines += ["", "Template: %s" % self.template_path]
            sections = [raw for raw, _, _ in headings(template or "")]
            if sections:
                lines.append("Sections it expects: %s" % ", ".join(sections))
            lines += [
                "",
                "Rewrite the description against the template, then retry. "
                "Every section must be present and actually filled in — "
                "delete the guidance comments rather than leaving them in "
                "place.",
            ]
        else:
            lines += ["", "Fix the description, then retry."]
        return "\n".join(lines)
