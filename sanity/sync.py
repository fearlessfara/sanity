"""Compile `.sanity/` into the instruction file each agent actually reads.

Claude reads `CLAUDE.md`, Codex reads `AGENTS.md`, Cursor reads
`.cursor/rules/*.mdc`. Without this they drift: three files saying almost the
same thing, none of them matching what the hooks enforce, and a model
confidently following the copy that is six weeks stale.

So `.sanity/rules/*.md` is the source and these are outputs. Each rule
contributes its prose plus one line naming the check, because a model told
that a rule is mechanically enforced behaves differently from one handed a
suggestion. `.sanity/guidance/*.md` contributes prose only — it is the place
for anything that cannot be checked, and it is never presented as though it
could be.

Only the region between the markers is touched. Whatever else lives in your
`CLAUDE.md` is left exactly as it was.
"""

import os

from . import rules as rules_module

BEGIN = "<!-- sanity:begin -->"
END = "<!-- sanity:end -->"

PREAMBLE = (
    "The rules below are enforced mechanically by `sanity`, which runs as a "
    "hook on file edits and pull requests. A violation cancels the tool call "
    "and hands back the reason, so following them up front is faster than "
    "being corrected. Do not edit this section by hand — it is generated "
    "from `.sanity/`, and `sanity sync` will overwrite it."
)

MDC_HEADER = (
    "---\n"
    "description: Repository rules enforced by sanity\n"
    "alwaysApply: true\n"
    "---\n"
)

DEFAULT_TARGETS = {
    "claude": "CLAUDE.md",
    "codex": "AGENTS.md",
    "cursor": os.path.join(".cursor", "rules", "sanity.mdc"),
}


def render(rules, guidance=()):
    """The generated Markdown, without the markers around it."""
    out = ["## Repository rules", "", PREAMBLE, ""]

    if rules:
        for rule in rules:
            out.append("### %s" % rule.title())
            out.append("")
            body = rule.body()
            if body:
                out += [body, ""]
            note = "Enforced: %s." % rule.describe()
            if rule.severity == "warn":
                note += " Reported as a warning, not blocked."
            out += [note, ""]

    for title, body in guidance:
        out.append("### %s" % title)
        out.append("")
        if body:
            out += [body, ""]

    if not rules and not guidance:
        out += ["No rules are defined yet.", ""]

    return "\n".join(out).rstrip() + "\n"


def splice(existing, block):
    """Put `block` between the markers, leaving everything else alone."""
    wrapped = "%s\n%s%s\n" % (BEGIN, block, END)
    if existing and BEGIN in existing and END in existing:
        head, _, rest = existing.partition(BEGIN)
        _, _, tail = rest.partition(END)
        return head + wrapped + tail.lstrip("\n")
    if not existing or not existing.strip():
        return wrapped
    return existing.rstrip("\n") + "\n\n" + wrapped


def targets(config, root):
    """[(agent, absolute path, whole_file)] for the configured outputs."""
    configured = dict(DEFAULT_TARGETS)
    configured.update((config.get("sync") or {}).get("targets") or {})

    out = []
    for agent in sorted(configured):
        relative = configured[agent]
        if not relative:
            continue
        out.append((agent, os.path.join(root, relative), agent == "cursor"))
    return out


def _read(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""


def plan(root, config):
    """[(agent, path, new_text, changed)] plus the rule errors encountered."""
    found, errors = rules_module.load(root)
    guidance = rules_module.load_guidance(root)
    block = render(found, guidance)

    steps = []
    for agent, path, whole_file in targets(config, root):
        existing = _read(path)
        if whole_file:
            new_text = MDC_HEADER + "\n" + block
        else:
            new_text = splice(existing, block)
        steps.append((agent, path, new_text, new_text != existing))
    return steps, errors


def write(path, text):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
