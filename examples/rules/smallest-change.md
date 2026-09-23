# Prefer the smallest change that works

When fixing or implementing something, touch only what the task requires.
Do not rename nearby symbols, reformat untouched files, restructure adjacent
modules, or "improve" unrelated code unless the user asked for it.

```sanity
{
  "check": "judge",
  "surface": "change",
  "severity": "warn",
  "message": "scope the diff to the requested change"
}
```
