# Migrations must say whether they are reversible

A migration that cannot be rolled back is worth knowing about before it runs
against production, not during the incident. State it at the top of the file:

    -- REVERSIBLE: yes
    -- REVERSIBLE: no — drops customer_tier, restore from snapshot

If the answer is no, say what the recovery path is.

```sanity
{
  "paths": ["migrations/**/*.sql", "**/migrations/*.sql"],
  "require": "--\\s*REVERSIBLE:\\s*(yes|no)",
  "severity": "block",
  "message": "add a -- REVERSIBLE: yes|no header explaining the rollback path"
}
```
