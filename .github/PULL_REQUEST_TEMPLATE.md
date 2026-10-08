## What

<!-- What does this change? Name the layer: eyes / hands (actions) /
drivers / deciders / governor / trail / export / MCP / CLI / docs. -->

## Why

<!-- The problem or gap this closes. Link the issue if there is one. -->

## How verified

<!-- Receipts, not adjectives: paste the commands and the numbers. -->

- [ ] `python -m pytest tests/ -q` — green (report the count)
- [ ] `ghost-hands bench` — green (report the count)
- [ ] Browser-real claims proven on Chromium fixtures, not only FakeDriver
      (or this PR makes no browser-real claim)
- [ ] Live cases (`ghost-hands bench --live`) run, if this touches live behavior

## Rules check

- [ ] Zero runtime dependencies preserved (stdlib only; no Playwright,
      no Selenium, nothing vendored from another automation library)
- [ ] New actions are classified by the governor and recorded in the trail
      before they execute
- [ ] No secrets, keys, or credentials in code, tests, fixtures, or the trail
- [ ] Honest-status language intact — unproven things are still called unproven
