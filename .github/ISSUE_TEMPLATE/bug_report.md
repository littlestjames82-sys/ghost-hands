---
name: Bug report
about: Something in Ghost Hands behaved wrong — a driver, the governor, the trail, the CLI, or the MCP server
title: "[bug] "
labels: bug
---

**What happened**
A clear description of the wrong behavior.

**What you expected**

**Minimal reproduction**
Steps, plus the smallest script / steps.json / command that triggers it:

```python
# or paste the CLI command
```

**Trail excerpt**
If a run was involved, paste the relevant trail events (JSONL lines). The
trail is the receipt — it usually shows exactly where perception, the
governor, or the driver disagreed with reality. Redact anything private.

**Environment**
- Ghost Hands version (`ghost_hands.__version__` or `pip show ghost-hands`):
- Python version:
- Chromium/Chrome build (`--version`):
- Driver: ChromiumDriver / FakeDriver
- Eyes mode: DOM / AX
- OS:

**Governor context (if relevant)**
Policy in effect (allow/ask/deny classes, domain allowlist) and the
classification the action received:
