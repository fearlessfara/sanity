# sanity

**Stop AI slop. Force the agent to follow your rules.**

Models will narrate the edit, invent filler comments, skip your PR template,
and ignore `CLAUDE.md` when it is inconvenient. Sanity exists to whip that
behaviour back into line.

You write rules once in `.sanity/`. Sanity enforces them mechanically in
Claude Code, Cursor, and Codex (deny the tool call), in pre-commit (block the
commit), and in CI — and can optionally ask a model whether a commit or PR
actually matches the diff.

`CLAUDE.md` / `AGENTS.md` / `.cursor/rules` are persuasion. Sanity is not:
the edit is cancelled, or the commit fails, and the reason is handed back so
the agent has to fix it.

| Check | What it rejects |
| --- | --- |
| `sanity comments` | Comments that only restate the code, banner a section, or narrate the edit |
| `sanity pr` | PR/MR descriptions that ignore the repository template |
| `sanity rules` | Whatever you put in `.sanity/rules/` |
| `sanity judge` | *(opt-in)* Commit/PR text that does not match the staged diff |

Python 3.8+, **stdlib only**, no network required for the mechanical checks.

---

## Install

### Quick start (recommended)

```bash
pip install git+https://github.com/fearlessfara/sanity.git
cd your-repo
sanity init
pre-commit install
```

`sanity init` will:

1. Create `.sanity/` with a starter config and warn-mode rules  
2. Write / merge `.pre-commit-config.yaml`  
3. Install Cursor + Codex agent hooks  
4. Run `sanity sync` → update `CLAUDE.md`, `AGENTS.md`, `.cursor/rules`

Then commit those files so the whole team gets the same gates.

If you enable the optional AI judge later:

```bash
pre-commit install --hook-type commit-msg
```

### pre-commit only

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/fearlessfara/sanity
    rev: v0.4.0   # pin a tag
    hooks:
      - id: sanity-comments
      - id: sanity-rules
      # - id: sanity-judge   # commit-msg stage; needs judge.enabled + api
```

```bash
pre-commit install
```

### Plain git hook (no pre-commit framework)

```bash
pip install git+https://github.com/fearlessfara/sanity.git
sanity install-git-hook
```

### Claude Code plugin

```text
/plugin marketplace add fearlessfara/sanity
/plugin install sanity@sanity
```

### Cursor & Codex

Handled by `sanity init`, or:

```bash
sanity install-agent-hooks          # both
sanity install-agent-hooks --agent cursor
```

Codex will not run hooks until you trust them — run `/hooks` in the Codex CLI.

### CI

Copy [`examples/github-actions.yml`](examples/github-actions.yml) into your
app repo, or:

```yaml
- run: pip install git+https://github.com/fearlessfara/sanity.git
- run: sanity pr --github-event "$GITHUB_EVENT_PATH"
- run: sanity rules --strict $(git diff --name-only origin/${{ github.base_ref }}...HEAD)
- run: sanity sync --check
```

---

## How it fits together

```
.sanity/rules/*.md     ← your rules (prose for the model + a mechanical check)
        │
        ├─ sanity sync      → stuff them into CLAUDE.md / AGENTS.md / .cursor
        ├─ agent hooks      → deny the sloppy tool call mid-turn
        ├─ pre-commit       → block the commit if it still slipped through
        └─ CI + optional judge → unskippable backstop
```

The agent hook is the whip that matters most: the model gets the refusal in
the same turn and has to rewrite. Pre-commit catches humans and any editor.
CI is what `--no-verify` cannot dodge.

---

## Your own rules

One Markdown file per rule under `.sanity/rules/`:

````markdown
# No `console.log` in shipped code

Use the `logger` module. Structured output can be filtered by level and
correlated by request id; a bare `console.log` cannot.

```sanity
{
  "paths": ["src/**/*.ts", "src/**/*.tsx"],
  "deny": "\\bconsole\\.log\\s*\\(",
  "severity": "block",
  "message": "use logger.debug() instead"
}
```
````

| Key | Meaning |
| --- | --- |
| `paths` | Globs (`**` spans directories, `*` stops at one) |
| `deny` / `require` | Exactly one. `deny` = added lines; `require` = whole file |
| `severity` | `block` / `warn` / `off` |
| `surface` | `files` (default) or `pull_request` |
| `message` | Shown when it fires |

Prose is for the model. The fenced block is what gets enforced. Anything you
cannot express as `deny`/`require` belongs in `.sanity/guidance/` (compiled
into instruction files, never blocked).

After editing rules:

```bash
sanity sync          # rewrite instruction files
sanity sync --check  # CI: fail if they drifted
```

Starter rules (warn mode) ship with `sanity init`. More examples live in
[`examples/rules/`](examples/rules/).

---

## Built-in checks

### Comments

Only lines **this change added** — a pre-existing filler comment never blocks
a later commit.

Catches: restatement (`// increment the counter`), banners, narration
(`// Added a retry wrapper`), generic labels (`# Constants`).

Leaves alone: `WHY:` notes, `TODO`/`FIXME`, doc comments, linter directives,
license headers, comments with real reasoning.

Languages: JS/TS, Python, Go, Rust, Java, Kotlin, Swift, Scala, C#, C/C++,
PHP, Ruby, shell. Unknown extensions are skipped.

### Pull requests

Auto-finds `.github/pull_request_template.md` (and GitLab / common variants).

Requires every template section present and non-empty, guidance HTML comments
removed, checklist items kept, no `[describe]` / `TBD` placeholders. Refuses
`gh pr create --fill`.

This validates **structure**, not truth. For “does the body match the diff?”,
enable the judge.

---

## Optional AI judge

Off by default. Warn-only when enabled. Fails open on missing keys, timeouts,
or bad replies — a flaky model must never wedge a commit unless you ask it to.

```json
{
  "judge": {
    "enabled": true,
    "mode": "warn",
    "when": ["commit", "pr"],
    "provider": "api",
    "api": {
      "model": "gpt-4o-mini",
      "api_key_env": "OPENAI_API_KEY"
    }
  }
}
```

| Provider | Where | Needs |
| --- | --- | --- |
| `session` | Agent hooks — injects the criterion into the current turn | Nothing |
| `api` | `commit-msg` / CI / `sanity judge` | OpenAI-compatible API key |

```bash
sanity judge --commit --message-file .git/COMMIT_EDITMSG
sanity judge --pr --body-file pr.md
```

---

## Configuration

`sanity.example.json` lists every key. Resolution order (later wins):

1. Built-in defaults  
2. `~/.sanity.json` or `~/.claude/sanity.json`  
3. `<repo>/.sanity/config.json` or `<repo>/.sanity.json`  
4. Env: `SANITY_MODE`, `SANITY_COMMENTS_MODE`, `SANITY_PR_MODE`,
   `SANITY_RULES_MODE`, `SANITY_JUDGE_*`

```bash
sanity config    # print what is actually in effect
```

Modes per check: `block` (default), `warn`, `off`.

Before flipping a check to `block` on a mature repo:

```bash
sanity comments --whole-file $(git ls-files '*.ts')
sanity pr --body-file some-recent-pr.md
```

If real history fails, loosen the rules — don’t blame the team.

---

## CLI

```text
sanity init                     scaffold .sanity/, hooks, pre-commit, sync
sanity comments [FILES...]      filler-comment check
sanity rules [FILES...]         apply .sanity/rules
sanity pr [--body-file F]       PR template check
sanity judge --commit|--pr      optional AI match check
sanity sync [--check]           compile rules → instruction files
sanity install-agent-hooks      Cursor / Codex manifests
sanity install-git-hook         plain .git/hooks/pre-commit
sanity config                   show resolved config + loaded rules
sanity hook files|pr            agent hook entry (JSON on stdin)
```

---

## Development

```bash
git clone https://github.com/fearlessfara/sanity.git
cd sanity
pip install -e .
python3 -m unittest discover -s tests
```

144 tests, no dependencies, no network.

---

## Caution

Hooks run with your credentials. Treat a `.pre-commit-config.yaml` `rev` or a
Claude marketplace add the same as any other dependency — read it before you
trust it.

Bypass once: `SANITY_MODE=off git commit …` or `--no-verify`.

---

## License

MIT
