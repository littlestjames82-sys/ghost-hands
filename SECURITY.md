# Security Policy

## Reporting a vulnerability

Please report security issues privately — do **not** open a public issue
for a vulnerability.

- Preferred: GitHub's private vulnerability reporting —
  **Security → Report a vulnerability** on this repository
  (GitHub Security Advisories).
- Or contact Ghost Developer Studio through the studio site linked from
  the repository homepage.

We aim to acknowledge a report within a few days and will agree a
disclosure timeline with you.

## Scope — what Ghost Hands is, and is not

Ghost Hands is a governance and provenance layer for agent-driven
browser actions: classification, policy, approval gates, and a trail.
Be clear-eyed about the boundary when you assess it:

- The governor gates **the actions Ghost Hands executes**. It cannot
  govern what a separately credentialed service, a different tool, or a
  human does outside it.
- Policy is deterministic pattern matching over actions and targets —
  it raises the cost of accidents and common attack shapes; it is not a
  sandbox and not a proof. A determined adversary with arbitrary code
  execution in the same process is out of scope.
- Sessions saved with `save_session` contain cookies and localStorage —
  treat those files as credentials: never commit them, never share them.
- The OpenAI-compatible decider sends the page's element map and your
  goal to the endpoint **you** configure. Nothing is sent anywhere by
  default; with no endpoint configured, no model is called.
- Ghost Hands contains **no stealth or evasion features** by design. If
  you find behavior that disguises automation or bypasses a site's
  access controls, that is a bug — report it.

Vulnerabilities in the governor's classification (an action classed
below its real consequence), the record-before-execute guarantee, or
credential handling are squarely in scope and are the reports we most
want.
