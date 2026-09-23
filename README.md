# sanity

**Stop AI slop. Force the agent to follow your rules — the ones a linter cannot express.**

Regex and style belong in eslint / ruff / biome. Sanity is for taste and
judgment: scope the diff, don't invent APIs, be honest in the PR body, prefer
the smallest change that works.

You write those rules once in `.sanity/`. Sanity grades the change with an AI
judge at the end of an agent turn (and again in pre-commit / CI), then hands
the reason back so the model has to fix it before it can stop.

`CLAUDE.md` / `AGENTS.md` / `.cursor/rules` are persuasion. Sanity is not:
a stop hook hands the rules back into the turn, and pre-commit / CI can fail
the commit.

| Command | What it does |
| --- | --- |
| `sanity new` | Interactive rule author |
| `sanity judge --rules` | Grade every natural-language rule against the change |
| `sanity sync` | Compile rules into the instruction files agents actually read |

Python 3.8+, **stdlib only**. Needs an API key for pre-commit / CI (or a live
agent session for the stop-hook self-review).

---

## Install

Same shape as pre-commit: install the tool once, then add it to the repo.

```bash
pip install git+https://github.com/fearlessfara/sanity.git
cd your-repo
sanity init
```

`sanity init` writes the files you commit:

| File | Same idea as |
| --- | --- |
| `.sanity/rules/` | the rules themselves |
| `.cursor/hooks.json` | a Cursor project hook |
| `.codex/hooks.json` | a Codex hook |
| `.pre-commit-config.yaml` | a pre-commit repo entry |
| `CLAUDE.md`, `AGENTS.md`, `.cursor/rules` | instruction files (`sanity sync`) |

The hook scripts call `sanity` on `PATH`. They do not embed your machine's
paths. Open **this repo** as the Cursor project — hooks in a parent folder
are not loaded.

With no API key, a Cursor or Codex stop hook sends one self-review back into
the turn, then lets it stop. If a key is set, the API judge can send the
agent back until `loop_count` reaches 3 (`loop_limit: 3` on the Cursor stop
hook). Codex still asks you to trust hooks: run `/hooks` in the CLI.

Git and CI are the pre-commit path. Once per clone:

```bash
pre-commit install
```

That gate needs `OPENAI_API_KEY` (or the env name in your config). With no
key it skips, the same way a flaky model must not wedge a commit.

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/fearlessfara/sanity
    rev: v0.6.0
    hooks:
      - id: sanity-judge-rules
```

### Claude Code

Use the plugin. It ships its own hooks via `${CLAUDE_PLUGIN_ROOT}`, so you
do not copy scripts into the repo.

```text
/plugin marketplace add fearlessfara/sanity
/plugin install sanity@sanity
```

Still run `sanity init` in the repo so `.sanity/rules/` exists. Pass
`--skip-hooks` if you do not want the Cursor and Codex files.

### CI

Copy [`examples/github-actions.yml`](examples/github-actions.yml), or:

```yaml
- run: pip install git+https://github.com/fearlessfara/sanity.git
- run: git reset --soft origin/${{ github.base_ref }} && sanity judge --rules
- run: sanity sync --check
```

---

## How it fits together

```
.sanity/rules/*.md     ← natural-language rules (prose = grading criterion)
        │
        ├─ sanity sync         → CLAUDE.md / AGENTS.md / .cursor
        ├─ agent stop hooks    → one self-review, then the turn may stop
        ├─ pre-commit          → sanity judge --rules
        └─ CI                  → same judge; fails open without a key
```

---

## Writing rules

```bash
sanity new
```

Or by hand — one Markdown file per rule under `.sanity/rules/`:

````markdown
# Prefer the smallest change that works

When fixing or implementing something, touch only what the task requires.
Do not rename nearby symbols, reformat untouched files, or "improve"
unrelated code unless the user asked for it.

```sanity
{
  "check": "judge",
  "surface": "change",
  "severity": "warn"
}
```
````

| Key | Meaning |
| --- | --- |
| `check` | Always `"judge"` |
| `criterion` | Optional override of the prose used as the grading standard |
| `severity` | `block` / `warn` / `off` |
| `surface` | `change` (default), `files`, or `pull_request` |
| `paths` | Optional globs to limit what the judge sees |
| `message` | Short note when it fires |

Non-interactive:

```bash
sanity new --title "Prefer the smallest change that works" \
  --body "Touch only what the task requires." \
  --severity warn -y
```

After editing rules:

```bash
sanity sync          # rewrite instruction files
sanity sync --check  # CI: fail if they drifted
```

---

## The judge loop

1. You write a rule in `.sanity/rules/`  
2. The agent finishes a turn → stop hook grades the working-tree change  
3. On failure (or session self-review): the agent is told to keep going  
4. The loop is capped the way Cursor caps `stop` (`loop_limit`, `loop_count`)  
5. Pre-commit / CI run `sanity judge --rules` against the staged diff  

| Provider | Where | Needs |
| --- | --- | --- |
| `auto` (default) | API when a key is present, else session inside an agent | API key *or* live agent |
| `session` | Agent stop / PR hooks — one-shot self-review | Nothing |
| `api` | pre-commit / CI / stop with a key | OpenAI-compatible API key |

Fails open on missing keys, timeouts, or unreadable replies.

### Optional: commit / PR vs diff

Separately, ask whether the commit message or PR body honestly describes the
change (`judge.enabled: true`):

```bash
sanity judge --commit --message-file .git/COMMIT_EDITMSG
sanity judge --pr --body-file pr.md
```

---

## Skipping a rule

In a PR body (or any text the judge sees):

```markdown
<!-- sanity-skip-file: smallest-change -->
```

| Directive | Effect |
| --- | --- |
| `sanity-skip-file: id` | Suppress a named NL rule for this change |
| `sanity-skip-file` | Suppress every NL rule |

---

## Configuration

`sanity.example.json` lists every key. Resolution order (later wins):

1. Built-in defaults  
2. `~/.sanity.json` or `~/.claude/sanity.json`  
3. `<repo>/.sanity/config.json`  
4. Env: `SANITY_MODE`, `SANITY_JUDGE_*`

```bash
sanity config
```

---

## CLI

```text
sanity init                     scaffold .sanity/, hooks, pre-commit, sync
sanity new                      create a natural-language rule
sanity judge --rules            grade NL rules against the change
sanity judge --commit|--pr      optional AI match check
sanity sync [--check]           compile rules → instruction files
sanity install-agent-hooks      Cursor / Codex stop + PR hooks
sanity install-git-hook         plain .git/hooks/pre-commit
sanity config                   show resolved config + loaded rules
sanity hook stop|pr             agent hook entry (JSON on stdin)
```

---

## Repository layout

| Path | What it is |
| --- | --- |
| `sanity/` | The Python package and the CLI |
| `sanity/starters/` | Files `sanity init` copies into a repo |
| `hooks/` | Claude Code plugin hooks (`${CLAUDE_PLUGIN_ROOT}`) |
| `examples/` | Pre-commit snippet, GitHub Actions workflow, sample rule |
| `tests/` | `unittest` suite |
| `.sanity/rules/` | Rules for this repository |

`CLAUDE.md`, `AGENTS.md`, and `.cursor/rules/sanity.mdc` are generated.
Edit `.sanity/rules/` and run `sanity sync`.

## Development

```bash
git clone https://github.com/fearlessfara/sanity.git
cd sanity
pip install -e .
python3 -m unittest discover -s tests
```

See [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.

---

## Caution

Hooks run with your credentials. Treat a `.pre-commit-config.yaml` `rev` or a
Claude marketplace add the same as any other dependency — read it before you
trust it.

Bypass once: `SANITY_MODE=off git commit …` or `--no-verify`.

---

## License

MIT
