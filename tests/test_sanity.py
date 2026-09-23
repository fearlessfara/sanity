"""python -m unittest discover -s tests   (or: pytest)"""

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from sanity import agents, claude, codex, cursor, rules, sync  # noqa: E402
from sanity import comments, config as config_module, init as init_module  # noqa: E402
from sanity import judge as judge_module, skip as skip_module  # noqa: E402
from sanity.pr import PRChecker, parse_command  # noqa: E402

TEMPLATE = """## Summary
<!-- What does this change and why? One or two sentences. -->

## Testing
<!-- How did you verify this? -->

## Checklist
- [ ] Added or updated tests
- [ ] Updated docs if behaviour changed
"""

GOOD_BODY = """## Summary
Reject cursors that decode to a negative offset; the paginator was looping.

## Testing
Added a unit test for the negative case and ran the integration suite.

## Checklist
- [x] Added or updated tests
- [ ] Updated docs if behaviour changed
"""


def checker(overrides=None):
    config = copy.deepcopy(config_module.DEFAULTS)
    if overrides:
        config = config_module._merge(config, overrides)
    return comments.CommentChecker(config)


def scan(source, path="a.ts", overrides=None):
    return checker(overrides).scan(path, source.splitlines())


class NoisyComments(unittest.TestCase):
    def test_restates_next_line(self):
        found = scan("// Fetch the user from the database\nconst u = db.fetchUser(id);")
        self.assertEqual(len(found), 1)
        self.assertIn("restates", found[0][2])

    def test_restates_same_line(self):
        found = scan("counter += 1; // increment the counter")
        self.assertEqual(len(found), 1)
        self.assertIn("same line", found[0][2])

    def test_generic_verb_shortcut(self):
        found = scan("// Loop through the items\nfor (const item of items) {}")
        self.assertEqual(len(found), 1)

    def test_banner(self):
        found = scan("// ---------------- Helpers ----------------\nfn();")
        self.assertEqual(len(found), 1)

    def test_narration(self):
        found = scan("// Added a retry wrapper here\nreturn retry(run);")
        self.assertIn("narrates", found[0][2])

    def test_boilerplate_label_python(self):
        found = scan("# Constants\nLIMIT = 5", path="a.py")
        self.assertEqual(len(found), 1)

    def test_single_line_block_comment(self):
        found = scan("/* Now we call the server */\nreturn client.Call(ctx);", path="a.go")
        self.assertEqual(len(found), 1)


class LegitimateComments(unittest.TestCase):
    CASES = [
        ("a.ts", "// Firestore caps batches at 500 writes.\nconst CHUNK = 500;"),
        ("b.ts", "// eslint-disable-next-line no-await-in-loop\nawait send(x);"),
        ("c.py", "# type: ignore[arg-type]\nx = f(y)"),
        ("d.go", "//go:generate mockgen -source=api.go\ntype API interface{}"),
        ("e.rs", "/// Returns the number of live sessions.\npub fn count() {}"),
        ("f.ts", "// Order matters: auth must run before rate limiting.\napp.use(auth);"),
        ("g.py", "# Retry with jitter; the provider 429s in bursts.\nretry(send)"),
        ("h.java", "// Copyright 2026 9fin Ltd.\nclass Foo {}"),
        ("i.ts", 'const url = "https://api.example.com"; // staging differs'),
        ("k.ts", "// Values below 0.5 round toward zero on V8, which breaks the tally.\n"
                 "const n = Math.round(x);"),
        ("l.c", "/* Buffer must outlive the callback; the driver keeps the pointer. */\n"
                "char *buf = alloc(64);"),
        ("m.py", '"""Persist a user record."""\ndb.save(user)'),
    ]

    def test_no_false_positives(self):
        for path, source in self.CASES:
            with self.subTest(path=path):
                self.assertEqual(scan(source, path=path), [], source)


class AddedLinesOnly(unittest.TestCase):
    def test_pre_existing_comment_is_ignored(self):
        old = "// Increment the counter\ncounter += 1;"
        new = ["// Increment the counter", "counter += 1;", "flush();"]
        added = comments.added_indices(old, new)
        self.assertEqual(checker().scan("a.ts", new, added), [])

    def test_added_comment_is_caught(self):
        old = "const total = a + b;"
        new = ["// Add a and b together", "const total = a + b;"]
        added = comments.added_indices(old, new)
        self.assertEqual(len(checker().scan("a.ts", new, added)), 1)


class CommentConfig(unittest.TestCase):
    SOURCE = "// Increment the counter\ncounter += 1;"

    def test_ignore_paths(self):
        found = scan(self.SOURCE, path="src/x.ts",
                     overrides={"comments": {"ignore_paths": ["src/**"]}})
        self.assertEqual(found, [])

    def test_rule_toggle(self):
        found = scan(self.SOURCE, overrides={"comments": {"rules": {
            "restatement": False, "generic_verb": False}}})
        self.assertEqual(found, [])

    def test_extra_allow_prefix(self):
        found = scan(self.SOURCE,
                     overrides={"comments": {"extra_allow_prefixes": ["increment"]}})
        self.assertEqual(found, [])

    def test_extra_deny_beats_builtin_allowlist(self):
        found = scan("// Because legacy reasons we ship it\nx();",
                     overrides={"comments": {"extra_deny_patterns": [
                         {"pattern": "legacy reasons", "reason": "vague hand-wave"}]}})
        self.assertEqual(found[0][2], "vague hand-wave")

    def test_unknown_extension_skipped(self):
        self.assertEqual(scan("# Imports\n# This is a heading", path="notes.md"), [])

    def test_extra_extension(self):
        found = scan(self.SOURCE, path="a.vue",
                     overrides={"comments": {"extra_extensions": {".vue": "//"}}})
        self.assertEqual(len(found), 1)

    def test_extra_lists_append_rather_than_replace(self):
        merged = config_module._merge(
            config_module.DEFAULTS,
            {"comments": {"extra_allow_patterns": ["FOO"]}},
        )
        self.assertEqual(merged["comments"]["extra_allow_patterns"], ["FOO"])
        twice = config_module._merge(
            merged, {"comments": {"extra_allow_patterns": ["BAR"]}}
        )
        self.assertEqual(twice["comments"]["extra_allow_patterns"], ["FOO", "BAR"])

    def test_ignore_paths_replaces(self):
        merged = config_module._merge(
            config_module.DEFAULTS, {"comments": {"ignore_paths": ["only/**"]}}
        )
        self.assertEqual(merged["comments"]["ignore_paths"], ["only/**"])


class CommandParsing(unittest.TestCase):
    def test_heredoc(self):
        command = (
            "git push && gh pr create --title 'fix: cursor' "
            "--body \"$(cat <<'EOF'\n%s\nEOF\n)\"" % GOOD_BODY
        )
        kind, title, body, _ = parse_command(command)
        self.assertEqual(kind, "github")
        self.assertEqual(title, "fix: cursor")
        self.assertIn("## Testing", body)

    def test_body_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as handle:
            handle.write(GOOD_BODY)
        _, _, body, _ = parse_command("gh pr create -t x --body-file %s" % handle.name)
        self.assertIn("## Summary", body)
        os.unlink(handle.name)

    def test_piped_heredoc(self):
        command = "cat <<'EOF' | gh pr create --body-file -\n%s\nEOF" % GOOD_BODY
        self.assertIn("## Checklist", parse_command(command)[2])

    def test_literal_newlines_are_unescaped(self):
        _, _, body, _ = parse_command(
            'gh pr create --title x --body "## Summary\\nIt works"'
        )
        self.assertIn("\n", body)

    def test_glab(self):
        kind, _, body, _ = parse_command("glab mr create --title x --description 'hi'")
        self.assertEqual((kind, body), ("gitlab", "hi"))

    def test_inline_equals_form(self):
        _, title, _, _ = parse_command("gh pr create --title='fix: thing'")
        self.assertEqual(title, "fix: thing")

    def test_flags_collected(self):
        self.assertIn("fill", parse_command("gh pr create --fill")[3])

    def test_unrelated_command(self):
        self.assertIsNone(parse_command("git status && npm test"))

    def test_mcp_payload(self):
        found = claude.pull_request({
            "tool_name": "mcp__abc__create_pull_request",
            "tool_input": {"title": "t", "body": "b"},
        })
        self.assertEqual(found[:3], ("github", "t", "b"))


class PRTemplate(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, ".github"))
        with open(os.path.join(self.root, ".github",
                               "pull_request_template.md"), "w") as handle:
            handle.write(TEMPLATE)

    def make(self, overrides=None):
        config = copy.deepcopy(config_module.DEFAULTS)
        if overrides:
            config = config_module._merge(config, overrides)
        return PRChecker(config, root=self.root)

    def problems(self, body, title=None, overrides=None, flags=()):
        pr = self.make(overrides)
        return pr.check(title, body, pr.find_template(), flags)

    def test_good_body_passes(self):
        self.assertEqual(self.problems(GOOD_BODY), [])

    def test_missing_sections(self):
        labels = [p[0] for p in self.problems("## Summary\nA real summary here.")]
        self.assertEqual(labels.count("missing section"), 2)

    def test_empty_section(self):
        body = GOOD_BODY.replace(
            "Added a unit test for the negative case and ran the integration suite.",
            "",
        )
        self.assertIn("empty section", [p[0] for p in self.problems(body)])

    def test_template_comment_left_in(self):
        body = GOOD_BODY.replace(
            "Reject cursors",
            "<!-- What does this change and why? One or two sentences. -->\nReject cursors",
        )
        self.assertIn("template comment left in", [p[0] for p in self.problems(body)])

    def test_checklist_dropped(self):
        body = GOOD_BODY.replace("- [ ] Updated docs if behaviour changed", "")
        self.assertIn("checklist item dropped", [p[0] for p in self.problems(body)])

    def test_placeholder(self):
        body = GOOD_BODY.replace("Reject cursors", "[describe the change]")
        self.assertIn("unfilled placeholder", [p[0] for p in self.problems(body)])

    def test_empty_body_and_fill(self):
        labels = [p[0] for p in self.problems("", flags={"fill"})]
        self.assertIn("empty description", labels)

    def test_checklist_only_section_is_not_empty(self):
        self.assertNotIn("empty section", [p[0] for p in self.problems(GOOD_BODY)])

    def test_contains_match_tolerates_longer_heading(self):
        body = GOOD_BODY.replace("## Summary", "## Summary of changes")
        strict = [p[0] for p in self.problems(body)]
        self.assertIn("missing section", strict)
        loose = [p[0] for p in self.problems(
            body, overrides={"pull_request": {"section_match": "contains"}})]
        self.assertNotIn("missing section", loose)

    def test_custom_section_list(self):
        found = self.problems(
            "## Why\nbecause the cursor loops",
            overrides={"pull_request": {
                "require_sections": ["Why"],
                "require_checklist_items": "ignore",
            }},
        )
        self.assertEqual(found, [])

    def test_ticket_required(self):
        labels = [p[0] for p in self.problems(
            GOOD_BODY, title="fix: cursor",
            overrides={"pull_request": {"require_ticket": {"enabled": True}}})]
        self.assertIn("no ticket reference", labels)

        labels = [p[0] for p in self.problems(
            GOOD_BODY, title="ENG-123 fix: cursor",
            overrides={"pull_request": {"require_ticket": {"enabled": True}}})]
        self.assertNotIn("no ticket reference", labels)

    def test_title_convention(self):
        labels = [p[0] for p in self.problems(
            GOOD_BODY, title="stuff",
            overrides={"pull_request": {"title": {"enabled": True}}})]
        self.assertIn("title convention", labels)

    def test_unticked_boxes_only_matter_when_asked(self):
        body = GOOD_BODY.replace("- [x]", "- [ ]")
        self.assertEqual(self.problems(body), [])
        labels = [p[0] for p in self.problems(
            body,
            overrides={"pull_request": {"require_checklist_items": "checked"}})]
        self.assertIn("checklist item unticked", labels)

    def test_gitlab_template_preferred_for_mr(self):
        os.makedirs(os.path.join(self.root, ".gitlab", "merge_request_templates"))
        path = os.path.join(self.root, ".gitlab",
                            "merge_request_templates", "Default.md")
        with open(path, "w") as handle:
            handle.write("## Why\n")
        pr = self.make()
        pr.find_template("gitlab")
        self.assertTrue(pr.template_path.endswith("Default.md"))


def rule_file(spec, prose="Because it matters."):
    return "# A rule\n\n%s\n\n```sanity\n%s\n```\n" % (prose, json.dumps(spec))


class RuleParsing(unittest.TestCase):
    def parse(self, spec, **kwargs):
        return rules.parse(rule_file(spec), "r.md", **kwargs)

    def test_id_defaults_to_the_filename(self):
        rule, error = self.parse({"deny": "x"}, identifier="no-x")
        self.assertIsNone(error)
        self.assertEqual(rule.id, "no-x")

    def test_prose_is_kept_for_the_instruction_files(self):
        rule, _ = self.parse({"deny": "x"})
        self.assertIn("Because it matters.", rule.prose)
        self.assertNotIn("deny", rule.prose)

    def test_missing_block(self):
        _, error = rules.parse("# Just prose\n", "r.md")
        self.assertIn("no ```sanity block", error)

    def test_unreadable_block(self):
        _, error = rules.parse("```sanity\n{nope}\n```\n", "r.md")
        self.assertIn("unreadable", error)

    def test_deny_and_require_together_is_rejected(self):
        _, error = self.parse({"deny": "x", "require": "y"})
        self.assertIn("exactly one", error)

    def test_neither_deny_nor_require_is_rejected(self):
        _, error = self.parse({"severity": "warn"})
        self.assertIn("exactly one", error)

    def test_bad_regex(self):
        _, error = self.parse({"deny": "([unclosed"})
        self.assertIn("bad regex", error)

    def test_unknown_severity(self):
        _, error = self.parse({"deny": "x", "severity": "loud"})
        self.assertIn("severity", error)

    def test_unknown_surface(self):
        _, error = self.parse({"deny": "x", "surface": "everything"})
        self.assertIn("surface", error)


class RuleMatching(unittest.TestCase):
    def build(self, spec, root=None):
        rule, error = rules.parse(rule_file(spec), "r.md", identifier="r")
        self.assertIsNone(error)
        return rules.RuleSet([rule], root=root)

    def test_deny_only_fires_on_added_lines(self):
        ruleset = self.build({"deny": "console\\.log"})
        lines = ["console.log(1);", "const a = 2;"]
        self.assertEqual(ruleset.scan("a.ts", lines, added={1}), [])
        self.assertEqual(len(ruleset.scan("a.ts", lines, added={0})), 1)

    def test_paths_filter(self):
        ruleset = self.build({"deny": "x", "paths": ["src/**/*.ts"]})
        self.assertEqual(ruleset.scan("test/a.ts", ["x"]), [])
        self.assertEqual(len(ruleset.scan("src/deep/a.ts", ["x"])), 1)

    def test_double_star_spans_zero_directories(self):
        """`src/**/*.ts` has to cover `src/a.ts`; fnmatch alone does not."""
        ruleset = self.build({"deny": "x", "paths": ["src/**/*.ts"]})
        self.assertEqual(len(ruleset.scan("src/a.ts", ["x"])), 1)

    def test_single_star_does_not_cross_a_slash(self):
        ruleset = self.build({"deny": "x", "paths": ["src/*.ts"]})
        self.assertEqual(ruleset.scan("src/deep/a.ts", ["x"]), [])

    def test_absolute_paths_are_matched_relative_to_the_root(self):
        ruleset = self.build({"deny": "x", "paths": ["src/*.ts"]}, root="/repo")
        self.assertEqual(len(ruleset.scan("/repo/src/a.ts", ["x"])), 1)

    def test_require_checks_the_whole_file(self):
        ruleset = self.build({"require": "REVERSIBLE"})
        self.assertEqual(len(ruleset.scan("m.sql", ["drop table t;"], added={0})), 1)
        self.assertEqual(
            ruleset.scan("m.sql", ["-- REVERSIBLE: no", "drop table t;"], added={1}),
            [],
        )

    def test_ignore_case(self):
        ruleset = self.build({"deny": "todo", "ignore_case": True})
        self.assertEqual(len(ruleset.scan("a.ts", ["TODO"])), 1)

    def test_pull_request_surface_is_not_scanned_as_a_file(self):
        ruleset = self.build({"deny": "x", "surface": "pull_request"})
        self.assertEqual(ruleset.scan("a.ts", ["x"]), [])
        self.assertEqual(len(ruleset.check_text("x")), 1)

    def test_severity_is_carried_through(self):
        ruleset = self.build({"deny": "x", "severity": "warn"})
        self.assertEqual(ruleset.scan("a.ts", ["x"])[0][3], "warn")


class RuleLoading(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.dir = os.path.join(self.root, ".sanity", "rules")
        os.makedirs(self.dir)

    def write(self, name, text):
        with open(os.path.join(self.dir, name), "w") as handle:
            handle.write(text)

    def test_loads_and_sorts(self):
        self.write("b.md", rule_file({"deny": "b"}))
        self.write("a.md", rule_file({"deny": "a"}))
        found, errors = rules.load(self.root)
        self.assertEqual([rule.id for rule in found], ["a", "b"])
        self.assertEqual(errors, [])

    def test_off_rules_are_not_loaded(self):
        self.write("a.md", rule_file({"deny": "a", "severity": "off"}))
        found, _ = rules.load(self.root)
        self.assertEqual(found, [])

    def test_a_broken_rule_is_reported_without_losing_the_others(self):
        self.write("good.md", rule_file({"deny": "a"}))
        self.write("broken.md", "```sanity\n{oops\n```\n")
        found, errors = rules.load(self.root)
        self.assertEqual([rule.id for rule in found], ["good"])
        self.assertEqual(len(errors), 1)

    def test_no_folder_is_not_an_error(self):
        found, errors = rules.load(tempfile.mkdtemp())
        self.assertEqual((found, errors), ([], []))


class Rendering(unittest.TestCase):
    def rule(self, spec, prose="# A title\n\nThe reasoning."):
        rule, error = rules.parse(
            "%s\n\n```sanity\n%s\n```\n" % (prose, json.dumps(spec)),
            "r.md", identifier="r",
        )
        self.assertIsNone(error)
        return rule

    def test_title_comes_from_the_heading(self):
        self.assertEqual(self.rule({"deny": "x"}).title(), "A title")

    def test_title_falls_back_to_the_id(self):
        self.assertEqual(self.rule({"deny": "x"}, prose="no heading").title(), "r")

    def test_describe_names_the_paths_and_pattern(self):
        rule = self.rule({"deny": "console\\.log", "paths": ["src/*.ts"]})
        self.assertEqual(rule.describe(), "`src/*.ts` must not match `console\\.log`")

    def test_describe_for_pull_requests(self):
        rule = self.rule({"deny": "x", "surface": "pull_request"})
        self.assertIn("PR and MR descriptions", rule.describe())

    def test_render_marks_warn_rules_as_unenforced(self):
        block = sync.render([self.rule({"deny": "x", "severity": "warn"})])
        self.assertIn("not blocked", block)

    def test_render_includes_prose_and_the_enforced_line(self):
        block = sync.render([self.rule({"deny": "x"})])
        self.assertIn("### A title", block)
        self.assertIn("The reasoning.", block)
        self.assertIn("Enforced:", block)

    def test_guidance_gets_no_enforced_line(self):
        block = sync.render([], [("Testing", "Prefer integration tests.")])
        self.assertIn("### Testing", block)
        self.assertNotIn("Enforced:", block)

    def test_empty_is_still_valid(self):
        self.assertIn("No rules are defined yet", sync.render([], []))


class Splicing(unittest.TestCase):
    def test_appends_when_there_are_no_markers(self):
        out = sync.splice("# Mine\n\nHand written.\n", "BLOCK\n")
        self.assertTrue(out.startswith("# Mine\n\nHand written.\n"))
        self.assertIn(sync.BEGIN, out)

    def test_replaces_between_markers_only(self):
        first = sync.splice("# Mine\n", "ONE\n")
        second = sync.splice(first, "TWO\n")
        self.assertIn("# Mine", second)
        self.assertIn("TWO", second)
        self.assertNotIn("ONE", second)

    def test_text_after_the_block_survives(self):
        existing = "%s\nOLD\n%s\n\n## Afterwards\n" % (sync.BEGIN, sync.END)
        out = sync.splice(existing, "NEW\n")
        self.assertIn("## Afterwards", out)
        self.assertNotIn("OLD", out)

    def test_is_idempotent(self):
        once = sync.splice("# Mine\n", "BLOCK\n")
        self.assertEqual(once, sync.splice(once, "BLOCK\n"))

    def test_empty_file(self):
        self.assertTrue(sync.splice("", "BLOCK\n").startswith(sync.BEGIN))


class SkipDirectives(unittest.TestCase):
    def test_parse_same_line(self):
        self.assertEqual(
            skip_module.parse_line("print(1)  # sanity-skip"),
            ("line", frozenset({"*"})),
        )

    def test_parse_named_rule(self):
        scope, ids = skip_module.parse_line("// sanity-skip: no-console-log")
        self.assertEqual(scope, "line")
        self.assertEqual(ids, frozenset({"no-console-log"}))

    def test_parse_next_line(self):
        self.assertEqual(
            skip_module.parse_line("# sanity-skip-next-line"),
            ("next", frozenset({"*"})),
        )

    def test_parse_file(self):
        scope, ids = skip_module.parse_line("/* sanity-skip-file: no-debug-print */")
        self.assertEqual(scope, "file")
        self.assertEqual(ids, frozenset({"no-debug-print"}))

    def test_skip_next_line_suppresses_comment_slop(self):
        source = (
            "// sanity-skip-next-line\n"
            "// Increment the counter\n"
            "counter += 1;\n"
        )
        self.assertEqual(scan(source), [])

    def test_skip_same_line_suppresses_comment_slop(self):
        source = "// Increment the counter  // sanity-skip\ncounter += 1;\n"
        self.assertEqual(scan(source), [])

    def test_skip_file_comments(self):
        source = (
            "// sanity-skip-file: comments\n"
            "// Increment the counter\n"
            "counter += 1;\n"
        )
        self.assertEqual(scan(source), [])

    def test_skip_does_not_silence_unrelated_lines(self):
        source = (
            "// sanity-skip-next-line\n"
            "const a = 1;\n"
            "// Increment the counter\n"
            "counter += 1;\n"
        )
        self.assertTrue(scan(source))

    def test_rule_skip_next_line(self):
        ruleset = rules.RuleSet([
            rules.parse(
                "# x\n\n```sanity\n%s\n```\n"
                % json.dumps({"deny": "console\\.log", "id": "no-console"}),
                "r.md", "no-console",
            )[0]
        ])
        lines = [
            "// sanity-skip-next-line: no-console",
            'console.log(1);',
        ]
        self.assertEqual(ruleset.scan("a.ts", lines), [])

    def test_rule_skip_named_only(self):
        no_console, _ = rules.parse(
            "# a\n\n```sanity\n%s\n```\n"
            % json.dumps({"deny": "console\\.log", "id": "no-console"}),
            "a.md", "no-console",
        )
        no_eval, _ = rules.parse(
            "# b\n\n```sanity\n%s\n```\n"
            % json.dumps({"deny": "\\beval\\(", "id": "no-eval"}),
            "b.md", "no-eval",
        )
        ruleset = rules.RuleSet([no_console, no_eval])
        lines = ['console.log(1); // sanity-skip: no-console', "eval(1);"]
        found = ruleset.scan("a.ts", lines)
        self.assertEqual(len(found), 1)
        self.assertIn("no-eval", found[0][2])

    def test_pr_body_skip(self):
        rule, _ = rules.parse(
            "# a\n\n```sanity\n%s\n```\n" % json.dumps({
                "surface": "pull_request",
                "deny": "Generated with",
                "id": "no-robots",
            }),
            "a.md", "no-robots",
        )
        ruleset = rules.RuleSet([rule])
        body = "Generated with Claude\n<!-- sanity-skip: no-robots -->\n"
        # skip-file style in HTML comment on another line — use file skip
        body = "<!-- sanity-skip-file: no-robots -->\nGenerated with Claude\n"
        self.assertEqual(ruleset.check_text(body), [])


PATCH = """*** Begin Patch
*** Update File: a.ts
@@
 function tick() {
+  // Increment the counter
   counter += 1;
 }
*** End Patch
"""


class CodexPayloads(unittest.TestCase):
    """Codex edits through apply_patch, so the patch has to be read."""

    def edits(self, patch):
        return codex.edits({
            "tool_name": "apply_patch", "tool_input": {"command": patch},
        })

    def test_added_comment_is_judged_against_context(self):
        (path, lines, added), = self.edits(PATCH)
        self.assertEqual(path, "a.ts")
        self.assertEqual(added, {1})
        violations = checker().scan(path, lines, added)
        self.assertIn("restates", violations[0][2])

    def test_pre_existing_comment_in_context_is_ignored(self):
        patch = PATCH.replace("+  // Increment", "   // Increment")
        (path, lines, added), = self.edits(patch)
        self.assertEqual(added, set())
        self.assertEqual(checker().scan(path, lines, added), [])

    def test_removed_lines_are_not_scanned(self):
        patch = PATCH.replace("+  // Increment", "-  // Increment")
        (_, lines, added), = self.edits(patch)
        self.assertEqual(added, set())
        self.assertNotIn("  // Increment the counter", lines)

    def test_add_file(self):
        patch = ("*** Begin Patch\n*** Add File: b.py\n"
                 "+# Increment the counter\n+counter += 1\n*** End Patch\n")
        (path, lines, added), = self.edits(patch)
        self.assertEqual((path, added), ("b.py", {0, 1}))
        self.assertTrue(checker().scan(path, lines, added))

    def test_delete_file_yields_nothing(self):
        patch = "*** Begin Patch\n*** Delete File: gone.ts\n*** End Patch\n"
        self.assertEqual(self.edits(patch), [])

    def test_multiple_files_in_one_patch(self):
        patch = ("*** Begin Patch\n*** Add File: a.ts\n+const a = 1;\n"
                 "*** Add File: b.ts\n+const b = 2;\n*** End Patch\n")
        self.assertEqual([path for path, _, _ in self.edits(patch)],
                         ["a.ts", "b.ts"])

    def test_bash_pr_command(self):
        found = codex.pull_request({
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr create --title x --body y"},
        })
        self.assertEqual(found[:3], ("github", "x", "y"))


class CursorPayloads(unittest.TestCase):
    """Shapes captured from a real Cursor 3.21.16 session."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.path = os.path.join(self.root, "a.ts")

    def write(self, content):
        payload = {
            "tool_name": "Write", "hook_event_name": "preToolUse",
            "workspace_roots": [self.root],
            "tool_input": {"file_path": self.path, "content": content},
        }
        return cursor.edits(payload)

    def test_new_file_scans_every_line(self):
        (path, lines, added), = self.write("// Constants\nx = 1;\n")
        self.assertEqual((path, added), (self.path, None))
        self.assertTrue(checker().scan(path, lines, added))

    def test_whole_file_write_only_scans_what_it_adds(self):
        with open(self.path, "w") as handle:
            handle.write("// Increment the counter\ncounter += 1;\n")

        (path, lines, added), = self.write(
            "// Increment the counter\ncounter += 1;\ncounter += 2;\n"
        )
        self.assertEqual(added, {2})
        self.assertEqual(checker().scan(path, lines, added), [],
                         "a comment already on disk must not block a later edit")

    def test_whole_file_write_still_catches_a_new_comment(self):
        with open(self.path, "w") as handle:
            handle.write("counter += 1;\n")

        (path, lines, added), = self.write(
            "// Increment the counter\ncounter += 1;\n"
        )
        self.assertEqual(added, {0})
        self.assertIn("restates", checker().scan(path, lines, added)[0][2])

    def test_after_file_edit_shape(self):
        found = cursor.edits({
            "hook_event_name": "afterFileEdit", "file_path": "a.ts",
            "edits": [{"old_string": "", "new_string": "// Constants\nx = 1;"}],
        })
        self.assertEqual(found[0][0], "a.ts")

    def test_payload_without_a_path_is_declined(self):
        self.assertEqual(cursor.edits({"tool_input": {"content": "x"}}), [])

    def test_shell_command_at_top_level(self):
        found = cursor.pull_request({
            "hook_event_name": "beforeShellExecution", "cwd": "",
            "command": "gh pr create --title x --body y",
        })
        self.assertEqual(found[:3], ("github", "x", "y"))

    def test_unrelated_shell_command(self):
        self.assertIsNone(cursor.pull_request({"command": "npm test"}))

    def test_empty_cwd_falls_back_to_workspace_roots(self):
        payload = {"cwd": "", "workspace_roots": [self.root]}
        start = payload.get("cwd") or payload.get("workspace_roots")
        self.assertEqual(config_module.repo_root(start[0]), self.root)


class AgentDetection(unittest.TestCase):
    def test_cursor_by_workspace_roots(self):
        self.assertIs(agents.detect({"workspace_roots": ["/repo"]}), cursor)

    def test_cursor_by_camel_case_event(self):
        self.assertIs(agents.detect({"hook_event_name": "preToolUse"}), cursor)

    def test_codex_by_apply_patch(self):
        self.assertIs(agents.detect({"tool_name": "apply_patch"}), codex)

    def test_codex_by_turn_id(self):
        self.assertIs(agents.detect({"turn_id": "t1", "tool_name": "Bash"}), codex)

    def test_claude_is_the_fallback(self):
        self.assertIs(
            agents.detect({"hook_event_name": "PreToolUse", "tool_name": "Write"}),
            claude,
        )

    def test_explicit_name_beats_detection(self):
        self.assertIs(agents.get("codex", {"workspace_roots": ["/repo"]}), codex)


class EndToEnd(unittest.TestCase):
    """Exercise the real entry points the way pre-commit and the agents do."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.env = dict(os.environ)
        self.env["CLAUDE_PROJECT_DIR"] = self.root
        self.env["PYTHONPATH"] = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), ".."
        )
        os.makedirs(os.path.join(self.root, ".github"))
        with open(os.path.join(self.root, ".github",
                               "pull_request_template.md"), "w") as handle:
            handle.write(TEMPLATE)

    def run_cli(self, args, stdin=""):
        return subprocess.run(
            [sys.executable, "-m", "sanity"] + args,
            input=stdin, capture_output=True, text=True,
            env=self.env, cwd=self.root,
        )

    def write_config(self, data):
        with open(os.path.join(self.root, ".sanity.json"), "w") as handle:
            json.dump(data, handle)

    def test_comments_cli_exit_1(self):
        path = os.path.join(self.root, "a.ts")
        with open(path, "w") as handle:
            handle.write("// Increment the counter\ncounter += 1;\n")
        result = self.run_cli(["comments", "a.ts", "--whole-file"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("restates", result.stdout)

    def test_claude_hook_exit_2(self):
        payload = json.dumps({
            "tool_name": "Write", "cwd": self.root,
            "tool_input": {"file_path": "a.ts",
                           "content": "// Increment the counter\ncounter += 1;"},
        })
        result = self.run_cli(["hook", "comments"], stdin=payload)
        self.assertEqual(result.returncode, 2)
        self.assertIn("restates", result.stderr)

    def test_pr_hook_blocks_bad_body(self):
        payload = json.dumps({
            "tool_name": "Bash", "cwd": self.root,
            "tool_input": {"command": "gh pr create --title x --body '## Summary\nhi there friend'"},
        })
        result = self.run_cli(["hook", "pr"], stdin=payload)
        self.assertEqual(result.returncode, 2)
        self.assertIn("missing section", result.stderr)

    def test_pr_hook_passes_good_body(self):
        payload = json.dumps({
            "tool_name": "mcp__x__create_pull_request", "cwd": self.root,
            "tool_input": {"title": "fix: cursor", "body": GOOD_BODY},
        })
        self.assertEqual(self.run_cli(["hook", "pr"], stdin=payload).returncode, 0)

    def test_warn_mode_never_blocks(self):
        self.write_config({"comments": {"mode": "warn"}})
        payload = json.dumps({
            "tool_name": "Write", "cwd": self.root,
            "tool_input": {"file_path": "a.ts",
                           "content": "// Increment the counter\ncounter += 1;"},
        })
        self.assertEqual(
            self.run_cli(["hook", "comments"], stdin=payload).returncode, 0
        )

    def test_off_mode(self):
        self.write_config({"comments": {"mode": "off"}})
        path = os.path.join(self.root, "a.ts")
        with open(path, "w") as handle:
            handle.write("// Increment the counter\ncounter += 1;\n")
        self.assertEqual(
            self.run_cli(["comments", "a.ts", "--whole-file"]).returncode, 0
        )

    def test_env_beats_config_file(self):
        self.write_config({"comments": {"mode": "block"}})
        self.env["SANITY_MODE"] = "off"
        path = os.path.join(self.root, "a.ts")
        with open(path, "w") as handle:
            handle.write("// Increment the counter\ncounter += 1;\n")
        self.assertEqual(
            self.run_cli(["comments", "a.ts", "--whole-file"]).returncode, 0
        )
        del self.env["SANITY_MODE"]

    def test_malformed_config_fails_open(self):
        with open(os.path.join(self.root, ".sanity.json"), "w") as handle:
            handle.write("{ not json ")
        path = os.path.join(self.root, "a.ts")
        with open(path, "w") as handle:
            handle.write("// Increment the counter\ncounter += 1;\n")
        result = self.run_cli(["comments", "a.ts", "--whole-file"])
        self.assertEqual(result.returncode, 1)

    def test_unrelated_bash_is_ignored(self):
        payload = json.dumps({
            "tool_name": "Bash", "cwd": self.root,
            "tool_input": {"command": "git status && npm test"},
        })
        self.assertEqual(self.run_cli(["hook", "pr"], stdin=payload).returncode, 0)

    def test_garbage_stdin_fails_open(self):
        self.assertEqual(
            self.run_cli(["hook", "comments"], stdin="not json").returncode, 0
        )

    def test_config_subcommand(self):
        result = self.run_cli(["config"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("pull_request", result.stdout)

    def test_codex_hook_denies_apply_patch(self):
        payload = json.dumps({
            "tool_name": "apply_patch", "cwd": self.root, "turn_id": "t1",
            "tool_input": {"command": PATCH},
        })
        result = self.run_cli(["hook", "comments"], stdin=payload)
        self.assertEqual(result.returncode, 2)
        decision = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("restates", decision["permissionDecisionReason"])

    def test_cursor_hook_denies_write(self):
        payload = json.dumps({
            "hook_event_name": "preToolUse", "cwd": self.root,
            "workspace_roots": [self.root],
            "tool_input": {"file_path": "a.ts",
                           "content": "// Increment the counter\ncounter += 1;"},
        })
        result = self.run_cli(["hook", "comments"], stdin=payload)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["permission"], "deny")

    def test_cursor_shell_hook_blocks_bad_pr(self):
        payload = json.dumps({
            "hook_event_name": "beforeShellExecution", "cwd": self.root,
            "command": "gh pr create --title x --body '## Summary\nhi there'",
        })
        result = self.run_cli(["hook", "pr", "--agent", "cursor"], stdin=payload)
        self.assertEqual(result.returncode, 2)
        self.assertIn("missing section", result.stderr)

    def test_warn_mode_never_blocks_any_agent(self):
        self.write_config({"comments": {"mode": "warn"}})
        payload = json.dumps({
            "tool_name": "apply_patch", "cwd": self.root,
            "tool_input": {"command": PATCH},
        })
        self.assertEqual(
            self.run_cli(["hook", "comments"], stdin=payload).returncode, 0
        )

    def test_dump_writes_the_payload(self):
        target = os.path.join(self.root, "payload.jsonl")
        payload = json.dumps({"tool_name": "Bash",
                              "tool_input": {"command": "npm test"}})
        self.run_cli(["hook", "pr", "--dump", target], stdin=payload)
        with open(target) as handle:
            self.assertEqual(json.loads(handle.read())["tool_name"], "Bash")

    def write_rule(self, name, spec, prose="# A rule\n\nBecause it matters."):
        directory = os.path.join(self.root, ".sanity", "rules")
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, name), "w") as handle:
            handle.write("%s\n\n```sanity\n%s\n```\n" % (prose, json.dumps(spec)))

    def test_rules_cli_blocks(self):
        self.write_rule("no-console.md", {"deny": "console\\.log"})
        with open(os.path.join(self.root, "a.ts"), "w") as handle:
            handle.write("console.log(1);\n")
        result = self.run_cli(["rules", "a.ts", "--whole-file"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("no-console", result.stdout)

    def test_warn_severity_reports_without_failing(self):
        self.write_rule("soft.md", {"deny": "console\\.log", "severity": "warn"})
        with open(os.path.join(self.root, "a.ts"), "w") as handle:
            handle.write("console.log(1);\n")
        result = self.run_cli(["rules", "a.ts", "--whole-file"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("soft", result.stdout)

    def test_folder_config_is_read(self):
        directory = os.path.join(self.root, ".sanity")
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, "config.json"), "w") as handle:
            json.dump({"comments": {"mode": "off"}}, handle)
        with open(os.path.join(self.root, "a.ts"), "w") as handle:
            handle.write("// Increment the counter\ncounter += 1;\n")
        self.assertEqual(
            self.run_cli(["comments", "a.ts", "--whole-file"]).returncode, 0
        )

    def test_folder_config_beats_the_flat_file(self):
        self.write_config({"comments": {"mode": "block"}})
        directory = os.path.join(self.root, ".sanity")
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, "config.json"), "w") as handle:
            json.dump({"comments": {"mode": "off"}}, handle)
        with open(os.path.join(self.root, "a.ts"), "w") as handle:
            handle.write("// Increment the counter\ncounter += 1;\n")
        self.assertEqual(
            self.run_cli(["comments", "a.ts", "--whole-file"]).returncode, 0
        )

    def test_broken_rule_fails_open_but_says_so(self):
        directory = os.path.join(self.root, ".sanity", "rules")
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, "broken.md"), "w") as handle:
            handle.write("```sanity\n{oops\n```\n")
        with open(os.path.join(self.root, "a.ts"), "w") as handle:
            handle.write("const a = 1;\n")

        result = self.run_cli(["rules", "a.ts", "--whole-file"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("skipping rule", result.stderr)

    def test_broken_rule_is_fatal_under_strict(self):
        directory = os.path.join(self.root, ".sanity", "rules")
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, "broken.md"), "w") as handle:
            handle.write("```sanity\n{oops\n```\n")
        result = self.run_cli(["rules", "--strict", "--whole-file"])
        self.assertEqual(result.returncode, 1)

    def test_rules_mode_off_disables_everything(self):
        self.write_rule("no-console.md", {"deny": "console\\.log"})
        self.env["SANITY_RULES_MODE"] = "off"
        with open(os.path.join(self.root, "a.ts"), "w") as handle:
            handle.write("console.log(1);\n")
        try:
            result = self.run_cli(["rules", "a.ts", "--whole-file"])
        finally:
            del self.env["SANITY_RULES_MODE"]
        self.assertEqual(result.returncode, 0)

    def test_edit_gate_applies_rules_as_well_as_comments(self):
        self.write_rule("no-console.md", {"deny": "console\\.log"})
        payload = json.dumps({
            "tool_name": "Write", "cwd": self.root,
            "tool_input": {"file_path": "a.ts", "content": "console.log(1);"},
        })
        result = self.run_cli(["hook", "files"], stdin=payload)
        self.assertEqual(result.returncode, 2)
        self.assertIn("no-console", result.stderr)

    def test_pr_rules_apply_to_the_body(self):
        self.write_rule("no-robots.md", {
            "surface": "pull_request", "deny": "Generated with",
        })
        body = GOOD_BODY + "\nGenerated with a robot\n"
        result = self.run_cli(["pr", "--body", body])
        self.assertEqual(result.returncode, 1)
        self.assertIn("no-robots", result.stdout)

    def test_a_rule_violation_is_not_called_a_template_mismatch(self):
        self.write_rule("no-robots.md", {
            "surface": "pull_request", "deny": "Generated with",
        })
        result = self.run_cli(["pr", "--body", GOOD_BODY + "\nGenerated with x"])
        self.assertIn("breaks the repository's rules", result.stdout)
        self.assertNotIn("does not match the repository template", result.stdout)

    def test_rules_do_not_suppress_the_template_report(self):
        self.write_rule("no-robots.md", {
            "surface": "pull_request", "deny": "Generated with",
        })
        result = self.run_cli(["pr", "--body", "## Summary\nthin\n\nGenerated with x"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("does not match the repository template", result.stdout)
        self.assertIn("no-robots", result.stdout)

    def test_config_lists_rules(self):
        self.write_rule("no-console.md", {"deny": "console\\.log"})
        result = self.run_cli(["config"])
        self.assertIn("no-console", result.stdout)

    def read(self, *parts):
        with open(os.path.join(self.root, *parts)) as handle:
            return handle.read()

    def test_sync_writes_every_instruction_file(self):
        self.write_rule("no-console.md", {"deny": "console\\.log"},
                        prose="# No console.log\n\nUse the logger.")
        result = self.run_cli(["sync"])
        self.assertEqual(result.returncode, 0)
        for name in ("CLAUDE.md", "AGENTS.md"):
            self.assertIn("No console.log", self.read(name))
        self.assertIn("alwaysApply", self.read(".cursor", "rules", "sanity.mdc"))

    def test_sync_preserves_hand_written_content(self):
        with open(os.path.join(self.root, "CLAUDE.md"), "w") as handle:
            handle.write("# House style\n\nKeep functions short.\n")
        self.write_rule("no-console.md", {"deny": "console\\.log"})
        self.run_cli(["sync"])
        self.assertIn("Keep functions short.", self.read("CLAUDE.md"))

    def test_sync_is_idempotent(self):
        self.write_rule("no-console.md", {"deny": "console\\.log"})
        self.run_cli(["sync"])
        first = self.read("CLAUDE.md")
        second_run = self.run_cli(["sync"])
        self.assertEqual(self.read("CLAUDE.md"), first)
        self.assertIn("already up to date", second_run.stdout)

    def test_sync_check_detects_drift(self):
        self.write_rule("no-console.md", {"deny": "console\\.log"})
        self.assertEqual(self.run_cli(["sync", "--check"]).returncode, 1)
        self.run_cli(["sync"])
        self.assertEqual(self.run_cli(["sync", "--check"]).returncode, 0)

    def test_sync_check_notices_an_edited_rule(self):
        self.write_rule("no-console.md", {"deny": "console\\.log"})
        self.run_cli(["sync"])
        self.write_rule("no-console.md", {"deny": "console\\.log"},
                        prose="# No console.log\n\nCompletely new reasoning.")
        result = self.run_cli(["sync", "--check"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("CLAUDE.md", result.stdout)

    def test_sync_includes_guidance(self):
        directory = os.path.join(self.root, ".sanity", "guidance")
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, "testing.md"), "w") as handle:
            handle.write("# Testing\n\nPrefer integration tests.\n")
        self.run_cli(["sync"])
        body = self.read("AGENTS.md")
        self.assertIn("Prefer integration tests.", body)

    def test_sync_target_can_be_switched_off(self):
        directory = os.path.join(self.root, ".sanity")
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, "config.json"), "w") as handle:
            json.dump({"sync": {"targets": {"codex": None}}}, handle)
        self.write_rule("no-console.md", {"deny": "console\\.log"})
        self.run_cli(["sync"])
        self.assertTrue(os.path.exists(os.path.join(self.root, "CLAUDE.md")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "AGENTS.md")))

    def test_install_agent_hooks(self):
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=False)
        result = self.run_cli(["install-agent-hooks"])
        self.assertEqual(result.returncode, 0)

        with open(os.path.join(self.root, ".cursor", "hooks.json")) as handle:
            cursor_manifest = json.load(handle)
        self.assertIn("preToolUse", cursor_manifest["hooks"])

        with open(os.path.join(self.root, ".codex", "hooks.json")) as handle:
            codex_manifest = json.load(handle)
        matchers = [entry["matcher"]
                    for entry in codex_manifest["hooks"]["PreToolUse"]]
        self.assertIn("^apply_patch$", matchers)

        shim = os.path.join(self.root, ".cursor", "hooks", "sanity-comments.py")
        self.assertTrue(os.access(shim, os.X_OK))

    def test_install_agent_hooks_keeps_foreign_entries(self):
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=False)
        os.makedirs(os.path.join(self.root, ".cursor"))
        path = os.path.join(self.root, ".cursor", "hooks.json")
        with open(path, "w") as handle:
            json.dump({"version": 1, "hooks": {
                "preToolUse": [{"command": ".cursor/hooks/format.sh"}]
            }}, handle)

        self.run_cli(["install-agent-hooks", "--agent", "cursor"])
        self.run_cli(["install-agent-hooks", "--agent", "cursor"])

        with open(path) as handle:
            commands = [entry["command"]
                        for entry in json.load(handle)["hooks"]["preToolUse"]]
        self.assertIn(".cursor/hooks/format.sh", commands)
        self.assertEqual(len(commands), 2, "reinstalling must not duplicate")


class InitCommand(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=False)
        self.env = dict(os.environ)
        self.env["CLAUDE_PROJECT_DIR"] = self.root
        self.env["PYTHONPATH"] = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), ".."
        )

    def run_cli(self, args):
        return subprocess.run(
            [sys.executable, "-m", "sanity"] + args,
            capture_output=True, text=True, env=self.env, cwd=self.root,
        )

    def test_init_seeds_rules_config_and_pre_commit(self):
        result = self.run_cli(["init", "--skip-hooks", "--skip-sync"])
        self.assertEqual(result.returncode, 0)
        self.assertTrue(os.path.exists(
            os.path.join(self.root, ".sanity", "config.json")
        ))
        rules_dir = os.path.join(self.root, ".sanity", "rules")
        self.assertGreaterEqual(len([n for n in os.listdir(rules_dir)
                                     if n.endswith(".md")]), 3)
        with open(os.path.join(self.root, ".pre-commit-config.yaml")) as handle:
            body = handle.read()
        self.assertIn("fearlessfara/sanity", body)
        self.assertIn("sanity-comments", body)
        self.assertIn("sanity-rules", body)

    def test_init_does_not_overwrite_existing_rules(self):
        os.makedirs(os.path.join(self.root, ".sanity", "rules"))
        custom = os.path.join(self.root, ".sanity", "rules", "no-console-log.md")
        with open(custom, "w") as handle:
            handle.write("# Mine\n\n```sanity\n{\"deny\": \"x\"}\n```\n")
        self.run_cli(["init", "--skip-hooks", "--skip-sync"])
        with open(custom) as handle:
            self.assertIn("# Mine", handle.read())

    def test_init_is_idempotent(self):
        self.run_cli(["init", "--skip-hooks", "--skip-sync"])
        with open(os.path.join(self.root, ".pre-commit-config.yaml")) as handle:
            first = handle.read()
        self.run_cli(["init", "--skip-hooks", "--skip-sync"])
        with open(os.path.join(self.root, ".pre-commit-config.yaml")) as handle:
            second = handle.read()
        self.assertEqual(first, second)

    def test_starters_parse(self):
        written, _ = init_module.seed_rules(self.root)
        self.assertTrue(written)
        found, errors = rules.load(self.root)
        self.assertEqual(errors, [])
        self.assertEqual(len(found), len(written))


class JudgeCommand(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=False)
        self.env = dict(os.environ)
        self.env["CLAUDE_PROJECT_DIR"] = self.root
        self.env["PYTHONPATH"] = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), ".."
        )

    def run_cli(self, args, stdin=""):
        return subprocess.run(
            [sys.executable, "-m", "sanity"] + args,
            input=stdin, capture_output=True, text=True,
            env=self.env, cwd=self.root,
        )

    def write_config(self, data):
        os.makedirs(os.path.join(self.root, ".sanity"), exist_ok=True)
        with open(os.path.join(self.root, ".sanity", "config.json"), "w") as handle:
            json.dump(data, handle)

    def test_disabled_is_a_noop(self):
        result = self.run_cli(["judge", "--commit", "--message", "fix: x"])
        self.assertEqual(result.returncode, 0)

    def test_session_provider_at_commit_explains_itself(self):
        self.write_config({
            "judge": {"enabled": True, "provider": "session", "mode": "warn"},
        })
        result = self.run_cli(["judge", "--commit", "--message", "fix: x"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("provider is `session`", result.stderr)

    def test_session_provider_on_pr_hook_advises(self):
        self.write_config({
            "judge": {"enabled": True, "provider": "session", "when": ["pr"]},
        })
        os.makedirs(os.path.join(self.root, ".github"))
        with open(os.path.join(self.root, ".github",
                               "pull_request_template.md"), "w") as handle:
            handle.write(TEMPLATE)
        payload = json.dumps({
            "tool_name": "mcp__x__create_pull_request", "cwd": self.root,
            "tool_input": {"title": "fix: cursor", "body": GOOD_BODY},
        })
        result = self.run_cli(["hook", "pr", "--agent", "claude"], stdin=payload)
        self.assertEqual(result.returncode, 0)
        self.assertIn("sanity judge", result.stdout)

    def test_api_without_key_fails_open(self):
        self.write_config({
            "judge": {
                "enabled": True, "provider": "api", "mode": "block",
                "api": {"api_key_env": "SANITY_TEST_MISSING_KEY"},
            },
        })
        self.env.pop("SANITY_TEST_MISSING_KEY", None)
        result = self.run_cli(["judge", "--commit", "--message", "fix: x"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("skipped", result.stdout + result.stderr)

    def test_parse_verdict(self):
        self.assertEqual(
            judge_module._parse_verdict('{"pass": false, "reason": "vague"}'),
            (False, "vague"),
        )
        self.assertIsNone(judge_module._parse_verdict("not json"))

    def test_api_block_mode_with_fake_transport(self):
        """Stub urllib so we can assert block without a network."""
        config = {
            "judge": {
                "enabled": True, "provider": "api", "mode": "block",
                "cache": False,
                "api": {"api_key_env": "SANITY_TEST_KEY", "timeout": 1},
            },
        }
        os.environ["SANITY_TEST_KEY"] = "sk-test"

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps({
                    "choices": [{"message": {
                        "content": '{"pass": false, "reason": "too vague"}'
                    }}]
                }).encode("utf-8")

        original = judge_module.urllib.request.urlopen
        judge_module.urllib.request.urlopen = lambda *a, **k: FakeResponse()
        try:
            passed, reason, channel = judge_module.evaluate(
                config, "commit", root=self.root, message="fix",
                diff="diff --git a/x b/x\n+hello\n",
            )
        finally:
            judge_module.urllib.request.urlopen = original
            os.environ.pop("SANITY_TEST_KEY", None)

        self.assertEqual((passed, channel), (False, "ok"))
        self.assertEqual(reason, "too vague")
        text = judge_module.report(
            "commit", passed, reason, channel, mode="block"
        )
        self.assertIn("blocked", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
