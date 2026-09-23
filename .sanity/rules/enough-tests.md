# Enough tests for the change

When you add or change behavior, cover the happy path and the failure path
for that behavior. Do not leave a behavior change relying only on tests
that never reach the new path.

```sanity
{
  "check": "judge",
  "surface": "change",
  "severity": "block",
  "message": "add tests for the happy path and the failure path"
}
```
