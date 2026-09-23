# No debug `print(` in Python application code

Temporary prints are fine while iterating. They are not fine in the commit:
they clutter CI logs and leak into production stdout.

Use the `logging` module (or your project's logger) instead.

```sanity
{
  "paths": ["**/*.py"],
  "deny": "(?m)^\\s*print\\s*\\(",
  "severity": "warn",
  "message": "use logging instead of print() in committed code"
}
```
