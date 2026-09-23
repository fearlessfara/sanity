# Contributing

Issues and pull requests are welcome.

## Setup

```bash
git clone https://github.com/fearlessfara/sanity.git
cd sanity
pip install -e .
python3 -m unittest discover -s tests
```

Python 3.8 or newer. The package uses the standard library only.

## What to change

Rules for this repository live in `.sanity/rules/`. After editing one, run `sanity sync` and commit the generated `CLAUDE.md`, `AGENTS.md`, and `.cursor/rules/sanity.mdc` with the rule.

`sanity/starters/` is what `sanity init` copies into someone else's repo. `examples/rules/` should stay in step with those starters.

Claude Code loads `hooks/hooks.json`. Cursor and Codex hooks are written into the target repo by `sanity init`, not stored here.

## Pull requests

Describe why the change is needed. Add or update a test when behavior changes, including the failure path. Run the unit tests before opening the PR.
