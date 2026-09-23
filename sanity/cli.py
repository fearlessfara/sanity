"""Command line for sanity.

  sanity init                   scaffold .sanity/, hooks, pre-commit, sync
  sanity new                    interactive NL rule author
  sanity judge --rules          grade check:\"judge\" rules (the product)
  sanity judge --commit|--pr    optional: does the text match the diff?
  sanity hook stop|pr           agent hook; follow-up continues the turn
  sanity sync [--check]         compile .sanity into the instruction files
  sanity install-agent-hooks    write .cursor/ and .codex/ stop hooks
  sanity config                 print the resolved config and its sources
"""

import argparse
import json
import os
import sys

from . import agents, config as config_module, gitinfo
from . import init as init_module, judge as judge_module, newrule, rules as rules_module, sync


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
    for error in errors:
        print("sanity: skipping rule — %s" % error, file=sys.stderr)
    return 1 if (errors and strict) else 0


def _apply_judge(config, kind, root=None, message=None, title=None, body=None,
                 agent=None):
    """Built-in commit/PR match. Returns (exit_code, advisory_text)."""
    if not judge_module.active(config, kind):
        return 0, ""

    opts = judge_module.settings(config)
    for_agent = agent is not None
    provider = judge_module.resolve_provider(config, for_agent=for_agent)

    if provider == "session" and not for_agent:
        print(
            "sanity judge: using session provider outside an agent hook — "
            "nothing to inject. Set judge.provider to `api` (or `auto`) for "
            "pre-commit / CI.",
            file=sys.stderr,
        )
        return 0, ""

    passed, reason, channel = judge_module.evaluate(
        config, kind, root=root, message=message, title=title, body=body,
        for_agent=for_agent,
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


def _apply_rule_judges(config, ruleset, surface, root=None, message=None,
                       title=None, body=None, agent=None, worktree=False):
    """Per-rule check:\"judge\". Returns (exit_code, advisory_text)."""
    if not ruleset or not ruleset.judged():
        return 0, ""

    for_agent = agent is not None
    provider = judge_module.resolve_rules_provider(config, for_agent=for_agent)

    if provider == "session" and not for_agent:
        print(
            "sanity judge: NL rules need `judge.provider=api` (or an agent "
            "hook with session/auto) to grade outside a live model turn.",
            file=sys.stderr,
        )
        return 0, ""

    results, text, blocking = judge_module.evaluate_rules(
        config, ruleset, surface=surface, root=root,
        message=message, title=title, body=body, for_agent=for_agent,
        worktree=worktree,
    )
    if not text:
        return 0, ""
    if for_agent:
        has_failure = any(
            (not passed) and channel == "ok"
            for _rule, passed, _reason, channel in results
        )
        is_session = any(
            channel == "session" for _r, _p, _reason, channel in results
        )
        if is_session or has_failure:
            return (1 if blocking else 0), text
        return 0, ""
    print(text, file=sys.stderr if blocking else sys.stdout)
    return (1 if blocking else 0), ""


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

    if args.kind in ("judge", "stop"):
        return _hook_judge(payload, config, agent, start)
    return _hook_pr(payload, config, start, agent)


def _hook_judge(payload, config, agent, start):
    if payload.get("stop_hook_active") is True:
        return 0
    try:
        loop_count = int(payload.get("loop_count") or 0)
    except (TypeError, ValueError):
        loop_count = 0
    if loop_count >= 3:
        return 0
    if payload.get("status") in ("aborted", "error"):
        return 0

    root = config_module.repo_root(start)
    ruleset = _ruleset(start)
    status, advice = _apply_rule_judges(
        config, ruleset, "change", root=root, agent=agent, worktree=True,
    )
    if advice:
        provider = judge_module.resolve_rules_provider(config, for_agent=True)
        if provider == "session" and loop_count >= 1:
            return 0
        if hasattr(agent, "followup"):
            return agent.followup(advice)
        return agent.advise(advice)
    if status:
        return agent.refuse(
            "sanity: natural-language rules failed — fix the change and retry."
        )
    return 0


def _hook_pr(payload, config, start, agent):
    """On PR create: grade pull_request-scoped NL rules (no template check)."""
    ruleset = _ruleset(start)
    if not ruleset:
        return 0
    try:
        found = agent.pull_request(payload)
    except Exception:
        return 0
    if not found:
        return 0

    _kind, title, body, _flags = found
    status, advice = _apply_rule_judges(
        config, ruleset, "pull_request",
        root=config_module.repo_root(start),
        title=title, body=body, agent=agent,
    )
    if advice:
        if hasattr(agent, "followup"):
            return agent.followup(advice)
        return agent.advise(advice)
    if status:
        return agent.refuse(
            "sanity: natural-language PR rules failed — rewrite the description."
        )
    return 0


GIT_HOOK = """#!/usr/bin/env bash
# Installed by `sanity install-git-hook`. Grades natural-language rules
# against the staged change.
# Skip once with:  SANITY_MODE=off git commit ...
set -uo pipefail

files=$(git diff --cached --name-only --diff-filter=ACMR)
[ -z "$files" ] && exit 0

if command -v sanity >/dev/null 2>&1; then
  sanity judge --rules || exit $?
else
  export PYTHONPATH="{home}${{PYTHONPATH:+:$PYTHONPATH}}"
  {python} -m sanity judge --rules || exit $?
fi
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


# Committed into the repo. No machine paths: pre-commit, Claude plugins, and
# Cursor's own hooks all work that way. The CLI is installed once per machine.
AGENT_SHIM = '''#!/usr/bin/env python3
"""Written by `sanity init`. Forwards stdin to the sanity CLI.

Install sanity once on the machine. This file stays free of local paths so
it can be committed, same as `.pre-commit-config.yaml`.
"""

import os
import shutil
import subprocess
import sys

_KIND = "__KIND__"
_AGENT = "__AGENT__"


def _command():
    found = shutil.which("sanity")
    if found:
        return found
    home = os.path.expanduser("~")
    for relative in (
        os.path.join(".local", "bin", "sanity"),
        os.path.join(".asdf", "shims", "sanity"),
    ):
        path = os.path.join(home, relative)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def main():
    command = _command()
    if not command:
        print(
            "sanity: CLI not on PATH, hook skipped. "
            "pip install git+https://github.com/fearlessfara/sanity.git",
            file=sys.stderr,
        )
        return 0
    return subprocess.call([command, "hook", _KIND, "--agent", _AGENT])


if __name__ == "__main__":
    sys.exit(main())
'''


def _shim(directory, kind, agent):
    os.makedirs(directory, exist_ok=True)
    hook_kind = "stop" if kind == "judge" else kind
    path = os.path.join(directory, "sanity-%s.py" % kind)
    text = AGENT_SHIM.replace("__KIND__", hook_kind).replace("__AGENT__", agent)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(path, 0o755)
    return path


def _merge_manifest(path, entries):
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


def _install_cursor(root):
    directory = os.path.join(root, ".cursor", "hooks")
    _shim(directory, "judge", "cursor")
    _shim(directory, "pr", "cursor")
    # loop_limit matches the stop-hook cap in _hook_judge (loop_count >= 3).
    # timeout leaves room for an API judge (default 30s) inside Cursor's hook.
    return _merge_manifest(
        os.path.join(root, ".cursor", "hooks.json"),
        {
            "beforeShellExecution": [
                {"command": ".cursor/hooks/sanity-pr.py", "timeout": 60}
            ],
            "beforeMCPExecution": [
                {"command": ".cursor/hooks/sanity-pr.py", "timeout": 60}
            ],
            "stop": [
                {
                    "command": ".cursor/hooks/sanity-judge.py",
                    "timeout": 60,
                    "loop_limit": 3,
                }
            ],
        },
    )


def _install_codex(root):
    directory = os.path.join(root, ".codex", "hooks")
    _shim(directory, "judge", "codex")
    _shim(directory, "pr", "codex")
    root_expr = '"$(git rev-parse --show-toplevel)"'
    return _merge_manifest(
        os.path.join(root, ".codex", "hooks.json"),
        {
            "PreToolUse": [
                {
                    "matcher": "^Bash$",
                    "hooks": [{
                        "type": "command",
                        "command": "%s/.codex/hooks/sanity-pr.py" % root_expr,
                        "statusMessage": "sanity: judging PR NL rules",
                    }],
                },
            ],
            "Stop": [
                {
                    "hooks": [{
                        "type": "command",
                        "command": "%s/.codex/hooks/sanity-judge.py" % root_expr,
                        "statusMessage": "sanity: judging NL rules",
                    }],
                },
            ],
        },
    )


def cmd_install_agent_hooks(args):
    root = config_module.repo_root(args.path)
    wanted = ["cursor", "codex"] if args.agent == "all" else [args.agent]

    for name in wanted:
        installer = _install_cursor if name == "cursor" else _install_codex
        print("wrote %s" % installer(root))

    if "codex" in wanted:
        print("\nCodex skips hooks until you trust them — run /hooks in the CLI.")
    print("Claude Code installs through the plugin marketplace instead; "
          "see the README.")
    return 0


def cmd_judge(args):
    config, _ = _load(args.path)
    root = config_module.repo_root(args.path)
    ruleset = _ruleset(root)

    if args.rules:
        title, body, message = args.title, args.body, args.message
        if args.body_file:
            try:
                with open(args.body_file, encoding="utf-8") as handle:
                    body = handle.read()
            except OSError:
                body = None
        if args.message_file:
            message = gitinfo.commit_message_file(args.message_file)
        surface = "pull_request" if (title or body) else "change"
        status, advice = _apply_rule_judges(
            config, ruleset, surface, root=root,
            title=title, body=body, message=message,
        )
        if advice:
            print(advice)
        return status

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
        rstatus, radvice = _apply_rule_judges(
            config, ruleset, "change", root=root, message=message
        )
        if radvice:
            print(radvice)
        return status or rstatus

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
    rstatus, radvice = _apply_rule_judges(
        config, ruleset, "pull_request", root=root, title=title, body=body
    )
    if radvice:
        print(radvice)
    return status or rstatus


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

    path, action = init_module.ensure_gitignore(root)
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
    print("Commit .sanity/, .cursor/, .codex/, CLAUDE.md, AGENTS.md,")
    print("and .pre-commit-config.yaml.")
    print()
    print("Cursor / Codex — open this repo as the project. No API key.")
    print("Git / CI       — pre-commit install, and set OPENAI_API_KEY.")
    print("Claude Code    — /plugin install sanity@sanity (hooks ship with it).")
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


def cmd_new(args):
    root = config_module.repo_root(args.path)
    interactive = not any([
        args.title, args.body, args.paths, args.message, args.criterion,
        args.id,
    ]) or args.interactive

    try:
        if interactive:
            defaults = {}
            if args.title:
                defaults["title"] = args.title
            if args.severity:
                defaults["severity"] = args.severity
            if args.surface:
                defaults["surface"] = args.surface
            fields = newrule.interview(defaults)
        else:
            fields = newrule.from_args(args)
        spec = newrule.build_spec(
            severity=fields.get("severity") or "warn",
            surface=fields.get("surface"),
            paths=fields.get("paths"),
            message=fields.get("message"),
            criterion=fields.get("criterion"),
        )
    except ValueError as error:
        print("sanity new: %s" % error, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\naborted", file=sys.stderr)
        return 130

    newrule.preview(fields, spec)
    if interactive and not args.yes:
        confirm = newrule._ask("Write this rule", default="y")
        if confirm.lower() not in ("y", "yes"):
            print("aborted")
            return 0

    try:
        path, rule = newrule.write_rule(
            root, fields["id"], fields["title"], fields.get("body") or "",
            spec, force=args.force,
        )
    except ValueError as error:
        print("sanity new: %s" % error, file=sys.stderr)
        return 1

    print("wrote %s" % os.path.relpath(path, root))
    print("  id=%s  check=judge  severity=%s  surface=%s" % (
        rule.id, rule.severity, rule.surface,
    ))

    do_sync = args.sync
    if do_sync is None and interactive and not args.yes:
        answer = newrule._ask(
            "Run `sanity sync` to update CLAUDE.md / AGENTS.md / .cursor",
            default="y",
        )
        do_sync = answer.lower() in ("y", "yes")
    elif do_sync is None:
        do_sync = True

    if do_sync:
        return cmd_sync(argparse.Namespace(
            path=root, check=False, strict=False,
        ))
    print("Remember: sanity sync")
    return 0


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

    create = sub.add_parser(
        "new",
        help="create a natural-language rule (interactive, or pass flags)",
    )
    create.add_argument("--path", help="repo root (default: cwd)")
    create.add_argument("--title", help="rule title (one line)")
    create.add_argument("--body", help="prose the judge grades against")
    create.add_argument(
        "--paths", action="append", default=[],
        help="path glob (repeatable)",
    )
    create.add_argument(
        "--surface", choices=["files", "change", "pull_request"],
    )
    create.add_argument(
        "--severity", choices=["warn", "block", "off"], default=None,
    )
    create.add_argument("--message", help="short message when it fires")
    create.add_argument("--criterion",
                        help="override prose used as the judge criterion")
    create.add_argument("--id", help="filename stem (default: slug of title)")
    create.add_argument("--force", action="store_true",
                        help="overwrite an existing rule file")
    create.add_argument("--yes", "-y", action="store_true",
                        help="skip the write confirmation")
    create.add_argument("--interactive", "-i", action="store_true",
                        help="force the questionnaire even if flags are set")
    sync_group = create.add_mutually_exclusive_group()
    sync_group.add_argument(
        "--sync", dest="sync", action="store_true", default=None,
        help="run sanity sync after writing (default in non-interactive mode)",
    )
    sync_group.add_argument(
        "--no-sync", dest="sync", action="store_false",
        help="skip sanity sync",
    )
    create.set_defaults(func=cmd_new)

    judge_cmd = sub.add_parser(
        "judge",
        help="AI check: NL rules and/or commit/PR vs diff",
    )
    kind = judge_cmd.add_mutually_exclusive_group(required=True)
    kind.add_argument("--rules", action="store_true",
                      help="grade check:\"judge\" rules against the change")
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
    hook.add_argument("kind", choices=["pr", "judge", "stop"])
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
