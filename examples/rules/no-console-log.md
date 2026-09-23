# No `console.log` in shipped code

Use the `logger` module. Structured output can be filtered by level and
correlated by request id in production; a bare `console.log` cannot, and it
is invisible once it reaches the aggregator.

Debugging locally is fine — just don't commit it.

```sanity
{
  "paths": ["src/**/*.ts", "src/**/*.tsx", "src/**/*.js"],
  "deny": "\\bconsole\\.(log|debug)\\s*\\(",
  "severity": "block",
  "message": "use logger.debug() so the line survives production filtering"
}
```
