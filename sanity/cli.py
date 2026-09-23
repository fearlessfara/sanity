"""Command line for sanity.

  sanity init                   scaffold .sanity/, hooks, pre-commit, sync
  sanity comments [FILES...]    pre-commit entry point; 1 on violations
  sanity rules [FILES...]       apply .sanity/rules; 1 on violations
  sanity pr [--body-file F]     check a PR body; 1 on violations
  sanity judge --commit|--pr    optional AI check (off by default)
  sanity hook files|comments|pr agent hook; 2 blocks the tool call
  sanity sync [--check]         compile .sanity into the instruction files
  sanity install-agent-hooks    write .cursor/ and .codex/ hook manifests
  sanity config                 print the resolved config and its sources
"""

import argparse
import json
import os
import sys

from . import agents, comments as comments_module, config as config_module, gitinfo
from . import init as init_module, judge as judge_module, rules as rules_module, sync
from .pr import PRChecker


def _load(start=None):
    return config_module.load(start)


def _ruleset(start=None):
    root = config_module.repo_root(start)
    try:
        found, errors = rules_module.load(root)
    except Exception:
        return rules_module.RuleSet(root=root)
    return rules_module.RuleSet(found, errors, root)


def _warn_about(errors, strict=False):
    """Unreadable rules are announced, and only fatal where CI can see them."""
    for error in errors:
        print("sanity: skipping rule — %s" % error, file=sys.stderr)
    return 1 if (errors and strict) else 0


# --------------------------------------------------------------- commands

def cmd_comments(args):
    config, _ = _load()
    checker = comments_module.CommentChecker(config)
    if checker.mode == "off":
        return 0

    paths = args.files or (gitinfo.staged_files() if gitinfo.in_repo() else [])
    if not paths:
        return 0

    failed = False
    for path in paths:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError:
            continue
        lines = text.splitlines()

        if args.whole_file:
            added = None
        else:
            head = gitinfo.head_content(path)
            added = comments_module.added_indices(head, lines) if head else None

        violations = checker.scan(path, lines, added)
        if violations:
            failed = True
            print(comments_module.report(path, violations))
            print()

    if not failed:
        return 0
    return 0 if checker.mode == "warn" else 1


def _severity(section_mode, rule_severity):
    """A section set to warn or off caps every rule underneath it."""
    if section_mode == "off" or rule_severity == "off":
        return "off"
    if section_mode == "warn":
        return "warn"
    return rule_severity


def _rule_violations(ruleset, section_mode, path, lines, added):
    graded = [
        (lineno, text, reason, _severity(section_mode, severity))
        for lineno, text, reason, severity in ruleset.scan(path, lines, added)
    ]
    return [entry for entry in graded if entry[3] != "off"]


def cmd_rules(args):
    config, _ = _load()
    section_mode = (config.get("rules", {}).get("mode") or "block").lower()
    ruleset = _ruleset()
    status = _warn_about(ruleset.errors, args.strict)
    if not ruleset or section_mode == "off":
        return status

    paths = args.files or (gitinfo.staged_files() if gitinfo.in_repo() else [])
    for path in paths:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                lines = handle.read().splitlines()
        except OSError:
            continue

        if args.whole_file:
            added = None
        else:
            head = gitinfo.head_content(path)
            added = comments_module.added_indices(head, lines) if head else None

        found = _rule_violations(ruleset, section_mode, path, lines, added)
        if found:
            print(rules_module.report(path, found))
            print()
            if any(entry[3] == "block" for entry in found):
                status = 1
    return status


def _severity(section_mode, rule_severity):
    """A section set to warn or off caps every rule underneath it."""
    if section_mode == "off" or rule_severity == "off":
        return "off"
    if section_mode == "warn":
        return "warn"
    return rule_severity


def _rule_violations(ruleset, section_mode, path, lines, added):
    graded = [
        (lineno, text, reason, _severity(section_mode, severity))
        for lineno, text, reason, severity in ruleset.scan(path, lines, added)
    ]
    return [entry for entry in graded if entry[3] != "off"]


def cmd_rules(args):
    config, _ = _load()
    section_mode = (config.get("rules", {}).get("mode") or "block").lower()
    ruleset = _ruleset()
    status = _warn_about(ruleset.errors, args.strict)
    if not ruleset or section_mode == "off":
        return status

    paths = args.files or (gitinfo.staged_files() if gitinfo.in_repo() else [])
    for path in paths:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                lines = handle.read().splitlines()
        except OSError:
            continue

        if args.whole_file:
            added = None
        else:
            head = gitinfo.head_content(path)
            added = comments_module.added_indices(head, lines) if head else None

        found = _rule_violations(ruleset, section_mode, path, lines, added)
        if found:
            print(rules_module.report(path, found))
            print()
            if any(entry[3] == "block" for entry in found):
                status = 1
    return status


def _pr_inputs(args):
    if args.github_event:
        try:
            with open(args.github_event, encoding="utf-8") as handle:
                event = json.load(handle)
        except (OSError, ValueError):
            return None, None
        request = event.get("pull_request") or {}
        return request.get("title"), request.get("body")

    body = args.body
    if args.body_file:
        source = sys.stdin if args.body_file == "-" else None
        if source:
            body = source.read()
        else:
            try:
                with open(args.body_file, encoding="utf-8") as handle:
                    body = handle.read()
            except OSError:
                body = None
    return args.title, body


def _pr_rule_problems(ruleset, config, title, body):
    section_mode = (config.get("rules", {}).get("mode") or "block").lower()
    if section_mode == "off":
        return [], False
    problems, blocking = [], False
    text = "\n".join(part for part in (title, body) if part)
    for identifier, detail, severity in ruleset.check_text(text):
        severity = _severity(section_mode, severity)
        if severity == "off":
            continue
        problems.append((identifier, detail))
        blocking = blocking or severity == "block"
    return problems, blocking


def _apply_judge(config, kind, root=None, message=None, title=None, body=None,
                 agent=None):
    """Run the optional AI judge. Returns (exit_code, advisory_text).

    exit_code is 1 only when mode is block and the judge failed.
    advisory_text is set for session provider (never blocks).
    """
    if not judge_module.active(config, kind):
        return 0, ""

    opts = judge_module.settings(config)
    if opts["provider"] == "session" and agent is None and kind == "commit":
        print(
            "sanity judge: provider is `session`, which only works inside an "
            "agent hook. Set judge.provider to `api` for pre-commit / CI, or "
            "leave the judge disabled.",
            file=sys.stderr,
        )
        return 0, ""

    passed, reason, channel = judge_module.evaluate(
        config, kind, root=root, message=message, title=title, body=body
    )
    text = judge_module.report(kind, passed, reason, channel, opts["mode"])
    if channel == "session":
        return 0, text
    if not text:
        return 0, ""
    print(text, file=sys.stderr if (not passed and opts["mode"] == "block")
          else sys.stdout)
    if not passed and opts["mode"] == "block" and channel == "ok":
        return 1, ""
    return 0, ""


def cmd_pr(args):
    config, _ = _load()
    checker = PRChecker(config)
    ruleset = _ruleset()

    title, body = _pr_inputs(args)
    if body is None and title is None:
        print("sanity pr: nothing to check (pass --body, --body-file or "
              "--github-event)", file=sys.stderr)
        return 0

    problems = []
    from_template = False
    blocking = False
    template = None

    if checker.mode != "off" or ruleset:
        template = checker.find_template()
        skipping = (
            not template
            and checker.config.get("skip_if_no_template", True)
            and not _strict(checker.config)
        )
        if not (skipping or checker.mode == "off"):
            problems = checker.check(title, body, template)
            from_template = bool(problems)
            blocking = from_template and checker.mode == "block"

        extra, rules_block = _pr_rule_problems(ruleset, config, title, body)
        problems += extra
        blocking = blocking or rules_block

        if problems:
            print(checker.report(problems, template, from_template))

    judge_status, _ = _apply_judge(config, "pr", title=title, body=body)
    if blocking or judge_status:
        return 1
    return 0


def _strict(config):
    return bool(
        config.get("min_body_words")
        or (config.get("require_ticket") or {}).get("enabled")
        or (config.get("title") or {}).get("enabled")
        or isinstance(config.get("require_sections"), list)
    )


def cmd_hook(args):
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw)
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0

    if args.dump:
        try:
            with open(args.dump, "a", encoding="utf-8") as handle:
                handle.write(raw.rstrip("\n") + "\n")
        except OSError:
            pass

    try:
        agent = agents.get(args.agent, payload)
    except KeyError:
        return 0
    start = payload.get("cwd") or payload.get("workspace_roots")
    if isinstance(start, list):
        start = start[0] if start else None

    try:
        config, _ = _load(start)
    except Exception:
        return 0

    if args.kind in ("comments", "files"):
        return _hook_files(payload, config, agent, start)
    return _hook_pr(payload, config, start, agent)


def _hook_files(payload, config, agent, start):
    """The edit gate: the built-in comment check plus any `files` rules."""
    checker = comments_module.CommentChecker(config)
    ruleset = _ruleset(start)
    section_mode = (config.get("rules", {}).get("mode") or "block").lower()
    if checker.mode == "off" and (section_mode == "off" or not ruleset):
        return 0

    try:
        targets = agent.edits(payload)
    except Exception:
        return 0

    blocking = warning = False
    reports = []
    for path, lines, added in targets:
        if checker.mode != "off":
            violations = checker.scan(path, lines, added)
            if violations:
                reports.append(comments_module.report(path, violations))
                blocking = blocking or checker.mode == "block"
                warning = warning or checker.mode == "warn"

        if section_mode != "off":
            found = _rule_violations(ruleset, section_mode, path, lines, added)
            if found:
                reports.append(rules_module.report(path, found))
                blocking = blocking or any(e[3] == "block" for e in found)
                warning = warning or any(e[3] == "warn" for e in found)

    if not reports:
        return 0
    message = "\n\n".join(reports)
    if not blocking and warning:
        print(message)
        return 0
    return agent.refuse(message)


def _hook_pr(payload, config, start, agent):
    checker = PRChecker(config, root=config_module.repo_root(start))
    ruleset = _ruleset(start)
    if checker.mode == "off" and not ruleset:
        return 0
    try:
        found = agent.pull_request(payload)
    except Exception:
        return 0
    if not found:
        return 0

    kind, title, body, flags = found
    if "draft" in flags and checker.config.get("skip_draft", False):
        return 0

    template = checker.find_template(kind)
    skipping = not template and checker.config.get("skip_if_no_template", True) \
        and not _strict(checker.config)

    problems = [] if (skipping or checker.mode == "off") else checker.check(
        title, body, template, flags
    )
    from_template = bool(problems)
    blocking = from_template and checker.mode == "block"

    extra, rules_block = _pr_rule_problems(ruleset, config, title, body)
    problems += extra
    blocking = blocking or rules_block

    if not problems:
        _, advice = _apply_judge(
            config, "pr", root=config_module.repo_root(start),
            title=title, body=body, agent=agent,
        )
        if advice:
            return agent.advise(advice)
        return 0

    message = checker.report(problems, template, from_template)
    if not blocking:
        print(message)
        _, advice = _apply_judge(
            config, "pr", root=config_module.repo_root(start),
            title=title, body=body, agent=agent,
        )
        if advice:
            return agent.advise(advice)
        return 0
    return agent.refuse(message)


GIT_HOOK = """#!/usr/bin/env bash
# Installed by `sanity install-git-hook`. Runs the comment check on staged
# files. Skip once with:  SANITY_MODE=off git commit ...
set -uo pipefail

files=$(git diff --cached --name-only --diff-filter=ACMR)
[ -z "$files" ] && exit 0

if command -v sanity >/dev/null 2>&1; then
  # shellcheck disable=SC2086
  exec sanity comments $files
fi

export PYTHONPATH="{home}${{PYTHONPATH:+:$PYTHONPATH}}"
# shellcheck disable=SC2086
exec {python} -m sanity comments $files
"""


def cmd_install_git_hook(args):
    root = config_module.repo_root(args.path)
    hooks = os.path.join(root, ".git", "hooks")
    if not os.path.isdir(hooks):
        print("no .git/hooks under %s" % root, file=sys.stderr)
        return 1

    target = os.path.join(hooks, "pre-commit")
    if os.path.exists(target) and not args.force:
        print("%s already exists; pass --force to replace it" % target,
              file=sys.stderr)
        return 1

    home = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(GIT_HOOK.format(home=home, python=sys.executable))
    os.chmod(target, 0o755)
    print("installed %s" % target)
    return 0


AGENT_SHIM = '''#!/usr/bin/env python3
"""Written by `sanity install-agent-hooks`. Reads a hook payload on stdin."""

import sys

try:
    from sanity.cli import main
except ImportError:
    sys.path.insert(0, {home!r})
    from sanity.cli import main

sys.exit(main(["hook", {kind!r}, "--agent", {agent!r}]))
'''


def _shim(directory, kind, agent, home):
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "sanity-%s.py" % kind)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(AGENT_SHIM.format(home=home, kind=kind, agent=agent))
    os.chmod(path, 0o755)
    return path


def _merge_manifest(path, entries):
    """Drop any previous sanity entries, add the current ones, keep the rest."""
    existing = _read_json(path) or {}
    hooks = existing.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}

    for event, additions in entries.items():
        kept = [
            entry for entry in (hooks.get(event) or [])
            if "sanity" not in json.dumps(entry)
        ]
        hooks[event] = kept + additions
    existing["hooks"] = hooks
    existing.setdefault("version", 1)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(existing, handle, indent=2)
        handle.write("\n")
    return path


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _install_cursor(root, home):
    directory = os.path.join(root, ".cursor", "hooks")
    _shim(directory, "comments", "cursor", home)
    _shim(directory, "pr", "cursor", home)
    return _merge_manifest(
        os.path.join(root, ".cursor", "hooks.json"),
        {
            "preToolUse": [{
                "command": ".cursor/hooks/sanity-comments.py",
                "matcher": "Write|Edit|MultiEdit",
            }],
            "beforeShellExecution": [
                {"command": ".cursor/hooks/sanity-pr.py"}
            ],
            "beforeMCPExecution": [
                {"command": ".cursor/hooks/sanity-pr.py"}
            ],
        },
    )


def _install_codex(root, home):
    directory = os.path.join(root, ".codex", "hooks")
    _shim(directory, "comments", "codex", home)
    _shim(directory, "pr", "codex", home)
    root_expr = '"$(git rev-parse --show-toplevel)"'
    return _merge_manifest(
        os.path.join(root, ".codex", "hooks.json"),
        {
            "PreToolUse": [
                {
                    "matcher": "^apply_patch$",
                    "hooks": [{
                        "type": "command",
                        "command": "%s/.codex/hooks/sanity-comments.py" % root_expr,
                        "statusMessage": "sanity: checking comments",
                    }],
                },
                {
                    "matcher": "^Bash$",
                    "hooks": [{
                        "type": "command",
                        "command": "%s/.codex/hooks/sanity-pr.py" % root_expr,
                        "statusMessage": "sanity: checking PR description",
                    }],
                },
            ],
        },
    )


def cmd_install_agent_hooks(args):
    root = config_module.repo_root(args.path)
    home = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    wanted = ["cursor", "codex"] if args.agent == "all" else [args.agent]

    for name in wanted:
        installer = _install_cursor if name == "cursor" else _install_codex
        print("wrote %s" % installer(root, home))

    if "codex" in wanted:
        print("\nCodex skips hooks until you trust them — run /hooks in the CLI.")
    print("Claude Code installs through the plugin marketplace instead; "
          "see the README.")
    return 0


def cmd_judge(args):
    config, _ = _load(args.path)
    root = config_module.repo_root(args.path)
    kind = "commit" if args.commit else "pr"

    if kind == "commit":
        message = args.message
        if args.message_file or (args.files and not message):
            path = args.message_file or (args.files[0] if args.files else None)
            if path:
                message = gitinfo.commit_message_file(path)
        if not message:
            print("sanity judge --commit: pass --message, --message-file, "
                  "or a commit-msg path", file=sys.stderr)
            return 0
        status, advice = _apply_judge(
            config, "commit", root=root, message=message
        )
        if advice:
            print(advice)
        return status

    title, body = args.title, args.body
    if args.body_file:
        try:
            with open(args.body_file, encoding="utf-8") as handle:
                body = handle.read()
        except OSError:
            body = None
    if body is None and title is None:
        print("sanity judge --pr: pass --body or --body-file", file=sys.stderr)
        return 0
    status, advice = _apply_judge(
        config, "pr", root=root, title=title, body=body
    )
    if advice:
        print(advice)
    return status


def cmd_init(args):
    root = config_module.repo_root(args.path)
    force = args.force

    path, created = init_module.seed_config(root, force=force)
    print("%s %s" % ("wrote" if created else "kept",
                     os.path.relpath(path, root)))

    written, kept = init_module.seed_rules(root, force=force)
    for path in written:
        print("wrote %s" % os.path.relpath(path, root))
    for path in kept:
        print("kept %s" % os.path.relpath(path, root))

    path, action = init_module.ensure_pre_commit(root, force=force)
    print("%s %s" % (action, os.path.relpath(path, root)))

    if not args.skip_hooks:
        hook_args = argparse.Namespace(path=root, agent=args.agent)
        cmd_install_agent_hooks(hook_args)

    if not args.skip_sync:
        sync_args = argparse.Namespace(
            path=root, check=False, strict=False
        )
        cmd_sync(sync_args)

    print()
    print("Next:")
    print("  1. pre-commit install          # if you use the pre-commit framework")
    print("  2. Edit .sanity/rules/         # then: sanity sync")
    print("  3. Optional AI judge: set judge.enabled=true in .sanity/config.json")
    print("     (provider=api + OPENAI_API_KEY for commit-time checks)")
    print("  4. Claude Code: install the sanity plugin from your marketplace")
    return 0


def cmd_sync(args):
    config, _ = _load(args.path)
    root = config_module.repo_root(args.path)
    steps, errors = sync.plan(root, config)
    status = _warn_about(errors, args.check or args.strict)

    stale = [step for step in steps if step[3]]
    if args.check:
        for _, path, _, _ in stale:
            print("out of date: %s" % os.path.relpath(path, root))
        if stale:
            sys.stdout.flush()
            print("\nRun `sanity sync` and commit the result.", file=sys.stderr)
            return 1
        print("instruction files are up to date")
        return status

    for _, path, text, changed in steps:
        if changed:
            sync.write(path, text)
            print("wrote %s" % os.path.relpath(path, root))
    if not stale:
        print("already up to date")
    return status


def cmd_config(args):
    config, sources = _load()
    ruleset = _ruleset()
    print("# sources (later wins)")
    for source in sources or ["(built-in defaults only)"]:
        print("#   %s" % source)

    print("# rules")
    for rule in ruleset.rules:
        print("#   %-24s %-13s %s" % (rule.id, rule.severity, rule.surface))
    for error in ruleset.errors:
        print("#   SKIPPED %s" % error)
    if not ruleset.rules and not ruleset.errors:
        print("#   (none in .sanity/rules)")

    print(json.dumps(config, indent=2, sort_keys=True))
    return 0


# ----------------------------------------------------------------- parser

def build_parser():
    parser = argparse.ArgumentParser(prog="sanity", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    initialise = sub.add_parser(
        "init",
        help="scaffold .sanity/, agent hooks, pre-commit config, and sync",
    )
    initialise.add_argument("--path", help="repo to initialise (default: cwd)")
    initialise.add_argument(
        "--agent", choices=["all", "cursor", "codex"], default="all"
    )
    initialise.add_argument("--force", action="store_true",
                            help="overwrite existing starter files")
    initialise.add_argument("--skip-hooks", action="store_true")
    initialise.add_argument("--skip-sync", action="store_true")
    initialise.set_defaults(func=cmd_init)

    comments = sub.add_parser(
        "comments", help="reject comments that only restate the code"
    )
    comments.add_argument("files", nargs="*")
    comments.add_argument(
        "--whole-file", action="store_true",
        help="check every line, not just those added since HEAD",
    )
    comments.set_defaults(func=cmd_comments)

    rules = sub.add_parser(
        "rules", help="apply the rules in .sanity/rules to staged files"
    )
    rules.add_argument("files", nargs="*")
    rules.add_argument(
        "--whole-file", action="store_true",
        help="check every line, not just those added since HEAD",
    )
    rules.add_argument(
        "--strict", action="store_true",
        help="fail on rules that cannot be parsed, rather than skipping them",
    )
    rules.set_defaults(func=cmd_rules)

    pull = sub.add_parser("pr", help="check a PR body against the template")
    pull.add_argument("--body")
    pull.add_argument("--body-file", help="path, or - for stdin")
    pull.add_argument("--title")
    pull.add_argument(
        "--github-event",
        help="path to a GitHub Actions event payload ($GITHUB_EVENT_PATH)",
    )
    pull.set_defaults(func=cmd_pr)

    judge_cmd = sub.add_parser(
        "judge",
        help="optional AI check that a commit/PR matches the diff (off by default)",
    )
    kind = judge_cmd.add_mutually_exclusive_group(required=True)
    kind.add_argument("--commit", action="store_true",
                      help="judge a commit message against the staged diff")
    kind.add_argument("--pr", action="store_true",
                      help="judge a PR description against the staged diff")
    judge_cmd.add_argument("files", nargs="*",
                           help="commit-msg file path (pre-commit commit-msg stage)")
    judge_cmd.add_argument("--message")
    judge_cmd.add_argument("--message-file")
    judge_cmd.add_argument("--title")
    judge_cmd.add_argument("--body")
    judge_cmd.add_argument("--body-file")
    judge_cmd.add_argument("--path", help="repo root (default: cwd)")
    judge_cmd.set_defaults(func=cmd_judge)

    hook = sub.add_parser("hook", help="agent hook (JSON on stdin)")
    hook.add_argument("kind", choices=["files", "comments", "pr"])
    hook.add_argument(
        "--agent", choices=agents.NAMES, default="auto",
        help="payload dialect to expect (default: guess from the payload)",
    )
    hook.add_argument(
        "--dump", metavar="PATH",
        help="append the raw payload here, to confirm an agent's field names",
    )
    hook.set_defaults(func=cmd_hook)

    install = sub.add_parser(
        "install-git-hook",
        help="write .git/hooks/pre-commit (for repos not using pre-commit)",
    )
    install.add_argument("--path", help="repo to install into (default: cwd)")
    install.add_argument("--force", action="store_true")
    install.set_defaults(func=cmd_install_git_hook)

    agent_hooks = sub.add_parser(
        "install-agent-hooks",
        help="write .cursor/hooks.json and .codex/hooks.json plus their shims",
    )
    agent_hooks.add_argument(
        "--agent", choices=["all", "cursor", "codex"], default="all"
    )
    agent_hooks.add_argument("--path", help="repo to install into (default: cwd)")
    agent_hooks.set_defaults(func=cmd_install_agent_hooks)

    compile_rules = sub.add_parser(
        "sync",
        help="compile .sanity into CLAUDE.md, AGENTS.md and .cursor/rules",
    )
    compile_rules.add_argument(
        "--check", action="store_true",
        help="report drift and exit 1 without writing (for CI)",
    )
    compile_rules.add_argument(
        "--strict", action="store_true",
        help="fail on rules that cannot be parsed, rather than skipping them",
    )
    compile_rules.add_argument("--path", help="repo to sync (default: cwd)")
    compile_rules.set_defaults(func=cmd_sync)

    show = sub.add_parser("config", help="print the resolved configuration")
    show.set_defaults(func=cmd_config)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
