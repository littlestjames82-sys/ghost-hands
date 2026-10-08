# Contributing to Ghost Hands

Ghost Hands is a Ghost Developer Studio project. Contributions are
welcome — the bar is receipts: if a change claims a capability, the bench
or the test suite proves it.

## Dev setup

```bash
git clone https://github.com/littlestjames82-sys/ghost-hands
cd ghost-hands
python3 -m venv .venv && source .venv/bin/activate
pip install -e . pytest
```

## Verify before you send

```bash
python -m pytest tests/ -q     # full suite
ghost-hands bench              # offline bench — must be fully green
ghost-hands bench --live       # opt-in: drives example.com + Wikipedia;
                               # needs real network (and a proxy in some
                               # sandboxes — the driver honors HTTPS_PROXY)
ghost-hands demo               # 30-second smoke of the whole loop
```

Browser-real claims must be proven on a real Chromium fixture case, not
only on `FakeDriver`. If no Chrome binary is found, the browser bench
cases skip with a note — set `GHOST_HANDS_CHROME` to your Chrome/Chromium
binary to run them.

## The rules (non-negotiable)

- **Zero runtime dependencies.** Standard library only. If a feature
  seems to need a package, it gets designed until it doesn't.
- **No Playwright, no Selenium** — not as a dependency, not vendored, not
  "just for this one thing." The driver stack is ours: a stdlib CDP
  client driving Chromium directly.
- **Everything is governed.** A new action kind gets a governor
  classification and is recorded in the trail before it executes.
  Consequential behavior is approval-gated by default.
- **No secrets.** Keys come from the environment only and are never
  stored, logged, or written to a trail. Don't commit credentials,
  session files, or trails from real sites.
- **Honest status.** Unproven things are called unproven — in the README,
  in the bench output, and in your PR description.

## Publishing

Releases are maintainer-run: version bump, full verification (pytest,
offline bench, live bench, `python -m build`, clean-venv install +
demo + MCP smoke), tag `vX.Y.Z`, and a GitHub release — the publish
workflow then ships to PyPI via trusted publishing. Don't publish,
tag, or dispatch workflows from a fork PR.

## Conduct

See `CODE_OF_CONDUCT.md`.
