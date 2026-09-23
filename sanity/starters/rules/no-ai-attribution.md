# No tool attribution in PR descriptions

A description explains the change to the next person reading it. Which editor
produced the diff is not part of that, and the trailer is usually the only
line nobody wrote on purpose.

```sanity
{
  "surface": "pull_request",
  "deny": "(?i)(generated with|co-authored-by:\\s*(claude|codex|cursor|copilot)|🤖)",
  "severity": "warn",
  "message": "drop the tool attribution line"
}
```
