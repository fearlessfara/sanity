"""python -m unittest discover -s tests"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from sanity import agents, claude, codex, cursor, rules, sync  # noqa: E402
from sanity import config as config_module, init as init_module  # noqa: E402
from sanity import judge as judge_module, newrule, skip as skip_module  # noqa: E402
from sanity.pr import parse_command  # noqa: E402


def rule_file(spec, prose="# A rule\n\nBecause it matters."):
    return "%s\n\n```sanity\n%s\n```\n" % (prose, json.dumps(spec))


class RuleParsing(unittest.TestCase):
    def test_judge_rule(self):
        rule, error = rules.parse(
            rule_file({"check": "judge", "severity": "warn"}),
            "r.md", "scope",
        )
        self.assertIsNone(error)
        self.assertTrue(rule.is_judge)
        self.assertIn("Because it matters.", rule.grading_criterion())

    def test_deny_is_rejected(self):
        _, error = rules.parse(
            rule_file({"deny": "x"}), "r.md", "no-x"
        )
        self.assertIn("linter", error)

    def test_needs_prose(self):
        text = "```sanity\n%s\n```\n" % json.dumps({"check": "judge"})
        _, error = rules.parse(text, "r.md", "empty")
        self.assertIn("prose or a criterion", error)

    def test_criterion_override(self):
        rule, error = rules.parse(
            rule_file({
                "check": "judge",
                "criterion": "No drive-by refactors.",
            }),
            "r.md", "r",
        )
        self.assertIsNone(error)
        self.assertEqual(rule.grading_criterion(), "No drive-by refactors.")

    def test_describe(self):
        rule, _ = rules.parse(
            rule_file({"check": "judge", "surface": "change"}),
            "r.md", "r",
        )
        self.assertIn("judged by AI", rule.describe())


class RuleLoading(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.dir = os.path.join(self.root, ".sanity", "rules")
        os.makedirs(self.dir)

    def write(self, name, text):
        with open(os.path.join(self.dir, name), "w") as handle:
            handle.write(text)

    def test_loads_judge_rules(self):
        self.write("a.md", rule_file({"check": "judge"}, "# A\n\nBody."))
        self.write("b.md", rule_file({"check": "judge"}, "# B\n\nBody."))
        found, errors = rules.load(self.root)
        self.assertEqual(errors, [])
        self.assertEqual([r.id for r in found], ["a", "b"])

    def test_off_skipped(self):
        self.write("a.md", rule_file({"check": "judge", "severity": "off"}))
        found, _ = rules.load(self.root)
        self.assertEqual(found, [])


class Sync(unittest.TestCase):
    def test_render_includes_enforced_line(self):
        rule, _ = rules.parse(
            rule_file({"check": "judge"}), "r.md", "r"
        )
        block = sync.render([rule])
        self.assertIn("### A rule", block)
        self.assertIn("Enforced:", block)
        self.assertIn("judged by AI", block)

    def test_splice_preserves_outside(self):
        out = sync.splice("# Mine\n", "BLOCK\n")
        self.assertTrue(out.startswith("# Mine\n"))
        self.assertIn(sync.BEGIN, out)


class Skip(unittest.TestCase):
    def test_file_skip_covers_named_rule(self):
        skips = skip_module.collect_text(
            "<!-- sanity-skip-file: smallest-change -->\nhello"
        )
        self.assertTrue(skips.covers("smallest-change"))
        self.assertFalse(skips.covers("other"))


class ParseCommand(unittest.TestCase):
    def test_gh_pr_create(self):
        found = parse_command(
            'gh pr create --title "fix: x" --body "hello world"'
        )
        self.assertEqual(found[0], "github")
        self.assertEqual(found[1], "fix: x")
        self.assertEqual(found[2], "hello world")


class NewRule(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def test_write_judge_rule(self):
        spec = newrule.build_spec(severity="warn", surface="change")
        path, rule = newrule.write_rule(
            self.root, "scope", "Scope", "Keep diffs small.", spec,
        )
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(rule.check, "judge")

    def test_slugify(self):
        self.assertEqual(newrule.slugify("Hello World!"), "hello-world")


class JudgeEngine(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def test_session_returns_advice(self):
        rule, _ = rules.parse(
            rule_file({"check": "judge", "surface": "change"}),
            "r.md", "scope",
        )
        ruleset = rules.RuleSet([rule], root=self.root)
        config = {"judge": {"provider": "session"}}
        results, advice, blocking = judge_module.evaluate_rules(
            config, ruleset, surface="change", root=self.root,
            diff="diff --git a/x b/x\n+hello\n", for_agent=True,
        )
        self.assertFalse(blocking)
        self.assertIn("scope", advice)
        self.assertEqual(results[0][3], "session")

    def test_api_failure_blocks(self):
        rule, _ = rules.parse(
            rule_file({"check": "judge", "severity": "block"}),
            "r.md", "scope",
        )
        ruleset = rules.RuleSet([rule], root=self.root)
        config = {
            "judge": {
                "provider": "api", "cache": False,
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
                        "content": '{"pass": false, "reason": "drive-by"}'
                    }}]
                }).encode("utf-8")

        original = judge_module.urllib.request.urlopen
        judge_module.urllib.request.urlopen = lambda *a, **k: FakeResponse()
        try:
            results, report, blocking = judge_module.evaluate_rules(
                config, ruleset, surface="change", root=self.root,
                diff="diff --git a/x b/x\n+rename everything\n",
            )
        finally:
            judge_module.urllib.request.urlopen = original
            os.environ.pop("SANITY_TEST_KEY", None)

        self.assertTrue(blocking)
        self.assertIn("drive-by", report)
        self.assertFalse(results[0][1])

    def test_auto_prefers_api_with_key(self):
        os.environ["OPENAI_API_KEY"] = "sk-test"
        try:
            provider = judge_module.resolve_rules_provider(
                {"judge": {"provider": "auto"}}, for_agent=True,
            )
        finally:
            os.environ.pop("OPENAI_API_KEY", None)
        self.assertEqual(provider, "api")


class CLI(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=False)
        subprocess.run(
            ["git", "config", "user.email", "t@example.com"],
            cwd=self.root, check=False, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "t"],
            cwd=self.root, check=False, capture_output=True,
        )
        seed = os.path.join(self.root, "README")
        with open(seed, "w") as handle:
            handle.write("seed\n")
        subprocess.run(
            ["git", "add", "README"], cwd=self.root, check=False,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-qm", "seed"], cwd=self.root, check=False,
            capture_output=True,
        )
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

    def write_rule(self):
        rules_dir = os.path.join(self.root, ".sanity", "rules")
        os.makedirs(rules_dir, exist_ok=True)
        with open(os.path.join(rules_dir, "scope.md"), "w") as handle:
            handle.write(rule_file(
                {"check": "judge", "surface": "change", "severity": "warn"},
                "# Scope\n\nKeep diffs small.",
            ))

    def test_new_noninteractive(self):
        result = self.run_cli([
            "new", "--title", "Prefer the smallest change",
            "--body", "Touch only what the task requires.",
            "--severity", "warn", "-y", "--no-sync",
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        path = os.path.join(
            self.root, ".sanity", "rules", "prefer-the-smallest-change.md"
        )
        self.assertTrue(os.path.isfile(path))

    def test_init_seeds_judge_hook(self):
        result = self.run_cli(["init", "--skip-hooks", "--skip-sync"])
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(os.path.join(self.root, ".pre-commit-config.yaml")) as handle:
            body = handle.read()
        self.assertIn("sanity-judge-rules", body)
        self.assertTrue(os.path.exists(
            os.path.join(self.root, ".sanity", "rules", "smallest-change.md")
        ))

    def test_stop_hook_session_followup(self):
        self.write_rule()
        self.write_config({"judge": {"provider": "session"}})
        with open(os.path.join(self.root, "touched.py"), "w") as handle:
            handle.write("x = 1\n")
        subprocess.run(
            ["git", "add", "touched.py"], cwd=self.root, check=False,
            capture_output=True,
        )
        payload = json.dumps({
            "cwd": self.root, "status": "completed", "loop_count": 0,
        })
        result = self.run_cli(
            ["hook", "stop", "--agent", "claude"], stdin=payload
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout.strip().splitlines()[0])
        self.assertEqual(data["decision"], "block")
        self.assertIn("scope", data["reason"])

    def test_stop_hook_loop_bound(self):
        self.write_rule()
        self.write_config({"judge": {"provider": "session"}})
        payload = json.dumps({
            "cwd": self.root, "status": "completed", "loop_count": 3,
        })
        result = self.run_cli(
            ["hook", "stop", "--agent", "claude"], stdin=payload
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")

    def test_cursor_followup_message(self):
        self.write_rule()
        self.write_config({"judge": {"provider": "session"}})
        with open(os.path.join(self.root, "touched.py"), "w") as handle:
            handle.write("x = 1\n")
        subprocess.run(
            ["git", "add", "touched.py"], cwd=self.root, check=False,
            capture_output=True,
        )
        payload = json.dumps({
            "cwd": self.root, "status": "completed", "loop_count": 0,
            "workspace_roots": [self.root],
        })
        result = self.run_cli(
            ["hook", "stop", "--agent", "cursor"], stdin=payload
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout.strip().splitlines()[0])
        self.assertIn("followup_message", data)

    def test_judge_rules_session_outside_agent(self):
        self.write_rule()
        self.write_config({"judge": {"provider": "session"}})
        result = self.run_cli(["judge", "--rules"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("NL rules need", result.stderr)

    def test_install_agent_hooks_stop_only(self):
        result = self.run_cli(["install-agent-hooks", "--agent", "cursor"])
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(os.path.join(self.root, ".cursor", "hooks.json")) as handle:
            data = json.load(handle)
        self.assertIn("stop", data["hooks"])
        self.assertNotIn("preToolUse", data["hooks"])
        stop = data["hooks"]["stop"][0]
        self.assertEqual(stop["command"], ".cursor/hooks/sanity-judge.py")
        self.assertEqual(stop["loop_limit"], 3)

        shim_path = os.path.join(self.root, ".cursor", "hooks", "sanity-judge.py")
        with open(shim_path) as handle:
            shim = handle.read()
        self.assertNotIn("sys.path.insert", shim)
        self.assertNotIn(self.root, shim)
        self.assertIn('"stop"', shim)
        self.assertIn('"cursor"', shim)

        env = dict(self.env)
        env["PATH"] = "/usr/bin:/bin"
        env["HOME"] = self.root
        skipped = subprocess.run(
            [sys.executable, shim_path],
            input="{}", capture_output=True, text=True, env=env, cwd=self.root,
        )
        self.assertEqual(skipped.returncode, 0, skipped.stderr)
        self.assertIn("not on PATH", skipped.stderr)

    def test_detect_agents(self):
        self.assertIs(agents.detect({"workspace_roots": ["/x"]}), cursor)
        self.assertIs(agents.detect({"turn_id": "1"}), codex)
        self.assertIs(agents.detect({"tool_name": "Bash"}), claude)


class ReportJudge(unittest.TestCase):
    def test_report(self):
        rule, _ = rules.parse(
            rule_file({"check": "judge", "severity": "block"}),
            "r.md", "scope",
        )
        text = rules.report_judge([
            (rule, False, "too broad", "ok", "block"),
        ])
        self.assertIn("blocked", text)
        self.assertIn("too broad", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
