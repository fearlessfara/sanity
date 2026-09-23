"""Detection of comments that carry no information the code doesn't."""

import difflib
import os
import re

from . import config as config_module

LINE_TOKEN = {
    ".ts": "//", ".tsx": "//", ".js": "//", ".jsx": "//", ".mjs": "//",
    ".cjs": "//", ".mts": "//", ".cts": "//",
    ".go": "//", ".rs": "//", ".java": "//", ".kt": "//", ".kts": "//",
    ".swift": "//", ".scala": "//", ".cs": "//", ".dart": "//", ".php": "//",
    ".c": "//", ".h": "//", ".cc": "//", ".cpp": "//", ".cxx": "//",
    ".hpp": "//", ".hh": "//", ".m": "//", ".mm": "//",
    ".py": "#", ".pyi": "#", ".rb": "#", ".sh": "#", ".bash": "#",
    ".zsh": "#", ".pl": "#",
}
BLOCK_TOKEN_LANGS = {"//"}

ALLOW_PREFIX = re.compile(
    r"^(why\b|todo|fixme|hack\b|xxx\b|note\b|nb\b|safety\b|warning\b|see\b|"
    r"ref\b|source\b|context\b|@|!|-\*-|copyright|spdx|licen[sc]e|"
    r"eslint|ts-|tslint|prettier|biome|oxlint|istanbul|c8 |v8 |"
    r"noqa|type:|pylint|mypy|flake8|ruff|fmt:|go:|nolint|nosec|"
    r"deno-|jshint|globals\b|region\b|endregion\b)",
    re.I,
)

ALLOW_SUBSTR = re.compile(
    r"\b(because|otherwise|workaround|work around|intentional|intentionally|"
    r"deliberate|deliberately|on purpose|must not|cannot|can't|do not|don't|"
    r"beware|careful|caveat|gotcha|assumes|assumption|invariant|"
    r"bug\b|quirk|edge case|race|deadlock|overflow|precision|rounding|"
    r"spec\b|rfc\s?\d|cve-|issue #|\bper \w+ docs?\b|upstream|legacy|"
    r"browser|safari|firefox|chrome|ie11|node \d|python 2|"
    r"perf\b|performance|hot path|o\(n|allocat|thread[- ]safe|"
    r"breaks|fails|crashes|panics|throws when|until |unless )\b"
    r"|https?://",
    re.I,
)

NARRATION = re.compile(
    r"^(added?|adding|new(ly)?|updated?|changed?|modif(y|ied)|remov(e|ed|ing)|"
    r"delet(e|ed)|fix(ed)?|refactor(ed)?|renamed?|moved?|switch(ed)? to|"
    r"now |here we|we (then |now )?|then we|let's|i (have |'ve )?|"
    r"this (is|was|will|does|function|method|class|variable|block|line|code|"
    r"section|part|bit|handles|creates|returns|sets|gets)|"
    r"step \d|first,|second,|third,|then,|finally,|next,|lastly,)",
    re.I,
)

BOILERPLATE = re.compile(
    r"^(imports?|constants?|types?|interfaces?|enums?|helpers?|utils?|"
    r"utilities|main|entry ?point|constructors?|getters?|setters?|props?|"
    r"state|variables?|fields?|methods?|functions?|classes?|exports?|"
    r"initiali[sz]ation|initiali[sz]e|setup|teardown|cleanup|config|"
    r"configuration|handlers?|routes?|hooks?|tests?|mocks?|fixtures?|"
    r"begin|start|end( of .*)?|done)"
    r"[\s:.\-=_*#]*$",
    re.I,
)

BANNER = re.compile(r"^[\s=\-_~*#+<>/|.]{4,}$")

GENERIC_VERB = re.compile(
    r"^(add|adds|set|sets|get|gets|create|creates|make|makes|build|builds|"
    r"return|returns|call|calls|check|checks|loop|loops|iterate|iterates|"
    r"initiali[sz]e|define|defines|declare|declares|import|imports|"
    r"export|exports|store|stores|save|saves|print|prints|log|logs|"
    r"convert|converts|parse|parses|handle|handles|close|closes|open|opens|"
    r"start|starts|stop|stops|increment|decrement|assign|assigns|"
    r"compute|computes|calculate|calculates|render|renders|send|sends|"
    r"filter|filters|map|sort|sorts|find|finds|append|push|pop|wrap|wraps)\b",
    re.I,
)

TRIM_EDGES = " \t=-_~*#+<>/|.:"

STOPWORDS = {
    "a", "an", "the", "to", "of", "for", "and", "or", "in", "on", "at", "by",
    "from", "as", "is", "are", "be", "we", "it", "its", "this", "that", "with",
    "into", "all", "each", "if", "then", "so", "do", "doe", "when", "use",
    "using", "new", "up", "out", "via", "per", "any", "one", "no", "not",
}


def stem(word):
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 5 and word.endswith("ing"):
        return word[:-3]
    if len(word) > 4 and word.endswith("ed"):
        return word[:-2]
    if len(word) > 4 and word.endswith("es"):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def tokens(text):
    """Stemmed word parts, splitting camelCase and snake_case."""
    out = []
    for chunk in re.findall(r"[A-Za-z][A-Za-z0-9]*", text or ""):
        for part in re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+", chunk):
            out.append(stem(part.lower()))
    return out


def mask_strings(line):
    """Blank quoted regions so tokens inside string literals are ignored."""
    out = []
    quote = None
    escaped = False
    for char in line:
        if quote:
            out.append(" ")
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in "\"'`":
            quote = char
            out.append(" ")
        else:
            out.append(char)
    return "".join(out)


def find_comment(line, token):
    """(code_before, comment_text, is_doc) or None."""
    masked = mask_strings(line)
    start = 0
    while True:
        index = masked.find(token, start)
        if index == -1:
            return None
        if token == "//" and index > 0 and masked[index - 1] == ":":
            start = index + 2
            continue
        break
    rest = line[index + len(token):]
    is_doc = token == "//" and rest.startswith("/")
    if token == "#" and rest.startswith("!") and index == 0:
        is_doc = True
    return line[:index].strip(), rest.lstrip("/#!").strip(), is_doc


def added_vs_file(path, new_lines):
    """Indices in new_lines that are not already in the file on disk.

    Agents that send a whole-file payload say nothing about what changed.
    Pre-tool hooks run before the write lands, so the old content is still
    readable and the scan can be narrowed to the lines this edit introduces.
    None means the file is new and every line counts.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            old = handle.read()
    except OSError:
        return None
    return added_indices(old, new_lines)


def added_indices(old_text, new_lines):
    """Indices in new_lines that the edit introduced."""
    if not old_text:
        return set(range(len(new_lines)))
    matcher = difflib.SequenceMatcher(
        None, old_text.splitlines(), new_lines, autojunk=False
    )
    out = set()
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag in ("insert", "replace"):
            out.update(range(j1, j2))
    return out


class CommentChecker:
    """Holds resolved config; one instance per run."""

    def __init__(self, config=None):
        section = (config or config_module.DEFAULTS)["comments"]
        rules = dict(config_module.DEFAULTS["comments"]["rules"])
        rules.update(section.get("rules") or {})
        thresholds = section.get("restatement") or {}

        self.mode = (section.get("mode") or "block").lower()
        self.rules = rules
        self.coverage = float(thresholds.get("coverage", 0.5))
        self.max_words = int(thresholds.get("max_words", 10))
        self.verb_max_words = int(thresholds.get("verb_max_words", 6))
        self.allow_prefixes = config_module.compiled(
            [r"^(?:%s)" % p for p in section.get("extra_allow_prefixes") or []],
            re.I,
        )
        self.allow_patterns = config_module.compiled(
            section.get("extra_allow_patterns"), re.I
        )
        self.deny_rules = config_module.compiled_rules(
            section.get("extra_deny_patterns"), re.I
        )
        self.extensions = dict(LINE_TOKEN)
        self.extensions.update(section.get("extra_extensions") or {})
        for ext in section.get("disable_extensions") or []:
            self.extensions.pop(ext.lower(), None)
        self.ignore_paths = section.get("ignore_paths") or []

    # ------------------------------------------------------------- helpers

    def ignored(self, path):
        return config_module.path_matches(path, self.ignore_paths)

    def token_for(self, path):
        return self.extensions.get(os.path.splitext(path)[1].lower())

    def classify(self, text, code_before, next_code):
        """Reason string if the comment is noise, else None."""
        body = (text or "").strip().strip(TRIM_EDGES)
        if not body:
            return (
                "decorative banner"
                if (text or "").strip() and self.rules["banner"]
                else None
            )

        # Configured denials are explicit, so they outrank the allowlist.
        for regex, reason in self.deny_rules:
            if regex.search(body):
                return reason

        if ALLOW_PREFIX.match(body) or ALLOW_SUBSTR.search(body):
            return None
        if any(r.match(body) for r in self.allow_prefixes):
            return None
        if any(r.search(body) for r in self.allow_patterns):
            return None

        if self.rules["banner"] and BANNER.match(body):
            return "decorative banner"
        if self.rules["boilerplate"] and BOILERPLATE.match(body):
            return "generic section label"
        if self.rules["narration"] and NARRATION.match(body):
            return "narrates the change instead of the code"

        if not (self.rules["restatement"] or self.rules["generic_verb"]):
            return None
        target = code_before or next_code
        if not target:
            return None

        words = [w for w in tokens(body) if w not in STOPWORDS]
        if not words or len(words) > self.max_words:
            return None
        code_words = set(tokens(target))
        if not code_words:
            return None

        hits = sum(1 for word in words if word in code_words)
        if not hits:
            return None
        where = "same line" if code_before else "next line"
        if self.rules["restatement"] and hits / len(words) >= self.coverage:
            return "restates the code on the %s" % where
        if (
            self.rules["generic_verb"]
            and len(words) <= self.verb_max_words
            and GENERIC_VERB.match(body)
        ):
            return "restates the code on the %s" % where
        return None

    @staticmethod
    def _next_code(lines, index, token):
        for candidate in range(index + 1, min(index + 4, len(lines))):
            text = lines[candidate].strip()
            if not text:
                continue
            if text.startswith(token) or text.startswith("*") or text.startswith("/*"):
                continue
            return text
        return ""

    # --------------------------------------------------------------- entry

    def scan(self, path, lines, added=None):
        """[(line_number, source_line, reason)] for the added lines."""
        if self.ignored(path):
            return []
        token = self.token_for(path)
        if not token:
            return []
        if added is None:
            added = set(range(len(lines)))

        violations = []
        in_block = False
        block_is_doc = False

        for index, raw in enumerate(lines):
            stripped = raw.strip()

            if token in BLOCK_TOKEN_LANGS:
                if in_block:
                    if index in added and not block_is_doc:
                        reason = self.classify(
                            stripped.lstrip("*").strip(),
                            "",
                            self._next_code(lines, index, token),
                        )
                        if reason:
                            violations.append((index + 1, stripped, reason))
                    if "*/" in stripped:
                        in_block = False
                    continue
                if stripped.startswith("/*"):
                    block_is_doc = stripped.startswith("/**")
                    if "*/" not in stripped:
                        in_block = True
                    if index in added and not block_is_doc:
                        reason = self.classify(
                            stripped[2:].replace("*/", "").strip(),
                            "",
                            self._next_code(lines, index, token),
                        )
                        if reason:
                            violations.append((index + 1, stripped, reason))
                    continue

            if index not in added:
                continue
            found = find_comment(raw, token)
            if not found:
                continue
            code_before, text, is_doc = found
            if is_doc:
                continue
            reason = self.classify(
                text, code_before, self._next_code(lines, index, token)
            )
            if reason:
                violations.append((index + 1, stripped, reason))

        return violations


def report(path, violations):
    lines = [
        "sanity blocked this edit — AI slop in comments (%d):" % len(violations),
        "",
    ]
    for lineno, text, reason in violations:
        lines.append("  %s:%s  %s" % (path, lineno, text))
        lines.append("      -> %s" % reason)
    lines += [
        "",
        "Rewrite the change without those comments, then retry the edit.",
        "Keep a comment only if it is:",
        "  - a WHY: note explaining reasoning the reader cannot infer",
        "  - TODO / FIXME",
        "  - a doc comment (/** */, ///, docstring) on public API",
        "  - a directive (eslint-, ts-, noqa, type:, go:) or a license header",
        "Do not narrate the edit. Do not restate the next line. Do not add banners.",
    ]
    return "\n".join(lines)
