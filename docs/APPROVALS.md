# Approval channels

Every action Ghost Hands takes is classified by the Governor
(`readonly` / `write` / `consequential`, or a policy pack's bundle).
`write` and `consequential` actions STOP and ask before they run —
record-before-execute means the trail already holds the governed
proposal when the question is asked. There are three channels an
"ask" can travel, plus the deliberate non-channel:

| Channel | Where the decision happens | How to select it |
| --- | --- | --- |
| **Prompt** | The terminal running the CLI | `ghost-hands run …` (default) |
| **GhostBus** | The task's requester replies on the bus | `ghost-hands bus-agent` (default `--approval-channel bus`) |
| **Phone** | Ryan's phone: a MrGhosty notification + in-app card | `ghost-hands run --approver phone …`, or `bus-agent --approval-channel phone` / env `GHOST_HANDS_APPROVAL_CHANNEL` |

And `--approve-all` answers every ask "yes" locally — an explicit,
loud opt-out of asking, never a default. Conflicting approver flags
(`--approve-all` with `--approver`, or two channels at once) are a
usage error (exit 2), never a silent pick.

Across all channels the rule is identical: **anything that is not an
explicit yes is a no.** Denied, expired, timed out, unreachable, or
unauthenticated all resolve to "do not execute", and the trail
records the verdict, the channel, and who decided.

## The phone channel

`PhoneApprover` (`src/ghost_hands/phone_approver.py`) talks to the
Ghost Hands bridge inside MrGhosty (v1.7.0+) — the same loopback,
pairing-token bridge the Android body uses
(`127.0.0.1:8378`, header `X-Ghost-Hands-Token`, token from
`GHOST_HANDS_ANDROID_TOKEN` or `--android-token`, never stored,
logged, or trailed). Non-loopback bridge hosts are refused unless
explicitly allowed in code; the CLI never allows them silently.

On an ask, Ghost Hands:

1. Builds a **redacted summary** (see below) and POSTs it:
   `POST /v1/approvals` with `{id (uuid4 hex), kind, classification,
   summary, target, url, timeout_seconds, test}` → `201` when the
   phone stored it as pending (`409` duplicate id, `400` bad
   payload).
2. Polls `GET /v1/approvals/<id>` about once a second until the
   phone answers `approved` / `denied` / `expired`, or the local
   timeout (default 600s) runs out.
3. Records trail `approval` events with `channel: "phone"`,
   `phase: "request"` then `"decision"`, the request id, the
   outcome, and `decided_by: "phone"` for phone-settled outcomes.

On the phone, MrGhosty raises a high-importance notification
("Ghost Hands needs approval", channel `ghost_hands_approvals`)
whose text is the summary, with **Approve** and **Deny** actions;
the same pending request appears as a card in MrGhosty's Device
tab with its kind, classification, target, URL, and remaining time.
Either surface settles the request — deciding cancels the
notification, and expired requests are swept from both.

The phone channel works with **every driver**: the bridge is just
an approver endpoint, so a desktop Chromium run can be approved
from the phone exactly like an Android-body run. In bus-agent
mode with `--approval-channel phone`, consequential asks route to
the phone; the agent still posts a bus comment noting the outcome
("approved on Ryan's phone, not on the bus") but never requests an
approval from the bus in that mode.

It is a **local bridge channel, not a cloud push service**: the
request travels over the same `adb forward`-reachable loopback
bridge as the Android body. If the bridge is unreachable, the ask
fails closed — the action does not run.

**On-device proof:** install MrGhosty v1.7.0, enable its
accessibility service, `adb forward tcp:8378 tcp:8378`, then run
`ghost-hands phone-approval-test`. It sends one harmless request
(kind `test`, summary "Test approval from Ghost Hands — approving
this runs nothing"), waits for the phone decision, prints
APPROVED / DENIED / EXPIRED / TIMEOUT, and exits 0 only on approval.

## The no-API-decision rule

The bridge API can **create** and **read** approval requests. It
has **no decision endpoint** — deliberately, and permanently:

> A token holder must never be able to approve their own request.

Possession of the pairing token proves a caller is Ryan's own
tooling on Ryan's own machine; it does not prove Ryan looked at
the request and said yes. So the only code paths that can settle
an approval are the two physical surfaces on the phone itself —
the notification actions (`GhostHandsApprovalReceiver`,
`exported=false`, reached only by explicit PendingIntents) and the
Device-tab card — both going through the approval store's
`decide()`, which only fires while the request is still pending
and unexpired. There is no HTTP method, on any bridge path, that
settles a request; the client-side contract tests pin that
(POST/PUT/DELETE against decision-shaped paths return 404/405 and
the request stays pending).

## The summary redaction rule

An approval summary is a label on a locked door, not a copy of
what's behind it. It carries only:

- the action **kind** and its **classification**;
- the **target descriptor** — tag, role, name, element number
  (e.g. `<button> button "Place order" [#14]`);
- the current **URL** (web) or **package** (Android), when known;
- for `type` actions: the target and the **character count** —
  never the typed text (it may be a password);
- for `fill_form`: the **field names** — never the values.

Summaries are capped (280 characters) so a notification stays a
glanceable question. The pairing token never appears in summaries,
trail events, error messages, or logs, on either side of the
bridge — and MrGhosty's bridge never logs summaries or the token
either.
