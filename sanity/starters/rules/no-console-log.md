# No `console.log` / `console.debug` in shipped code

Use a real logger. Structured output can be filtered by level and correlated
by request id; a bare `console.log` cannot, and it disappears in production
aggregators.

Debugging locally is fine — just don't commit it.

```sanity
{
  "paths": ["src/**/*.ts", "src/**/*.tsx", "src/**/*.js", "src/**/*.jsx"],
  "deny": "\\bconsole\\.(log|debug)\\s*\\(",
  "severity": "warn",
  "message": "use a logger so the line survives production filtering"
}
```
