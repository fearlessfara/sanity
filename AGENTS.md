<!-- sanity:begin -->
## Repository rules

These rules are enforced by `sanity` with an AI judge. On an agent turn the stop hook hands a violation back so you can fix it before stopping. On commit and in CI a blocking rule can fail the change. Do not ignore a rule below because it is inconvenient. Do not edit this section by hand; it is generated from `.sanity/` and `sanity sync` will overwrite it.

### Enough tests for the change

When you add or change behavior, cover the happy path and the failure path
for that behavior. Do not leave a behavior change relying only on tests
that never reach the new path.

Enforced: the staged / working-tree change — judged by AI against the rule prose.
<!-- sanity:end -->
