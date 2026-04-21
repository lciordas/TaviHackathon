## Spec for Vendor Contact / Auctioning

This document is a partial spec for the software component that implements step 3 of the tavi pipeline: "Vendor Contact / Auctioning".<br>
This document should be presented to Claude Code and completed together by prompting Claude Code to ask us for any missing information.

### Context

In Step 1, we gather the description of a work order and save it in the database.<br>
In Step 2, we identify a list of eligible vendors.<br>
In Step 3 - the current step - we manage the negotiation process with each of these vendors.<br>

### Data Layer

Two new tables are needed to store the data required.

#### `negotiations` table

A negotiation is a conversation with a given vendor regarding a specific work order.<br>
Each negotiation is represented by a row in this table.<br>

One row per `(work_order, vendor)` pair. <br> Holds the state and the
vendor's quote (if any). <br> Everything else — extracted facts, escalation notes,
terminal reason — goes in a JSON blob.<br> Message thread is in
`negotiation_messages`.

| Column                | Type        | Notes                                                                |
|-----------------------|-------------|----------------------------------------------------------------------|
| `id`                  | integer PK  |                                                                      |
| `work_order_id`       | integer FK  |                                                                      |
| `vendor_id`           | integer FK  |                                                                      |
| `state`               | enum        | See state list in spec.                                              |
| `quoted_price_cents`  | integer     | `NULL` until `QUOTED`.                                               |
| `quoted_available_at` | timestamp   | `NULL` until `QUOTED`.                                               |
| `escalated`           | bool        | Default `false`.                                                     |
| `attributes`          | JSON        | Freeform: insurance, license, scope notes, escalation reason, terminal reason, anything else the agent extracts. |
| `created_at`          | timestamp   | Server-default `now()`.                                              |
| `updated_at`          | timestamp   | Auto-updated on write.                                               |

#### `negotiation_messages` table

| Column          | Type        | Notes                                                                          |
|-----------------|-------------|--------------------------------------------------------------------------------|
| `id`            | integer PK  | Auto-increment.                                                                |
| `negotiation_id`| integer FK  | References `negotiations.id`.                                                  |
| `sender`        | enum        | `tavi` or `vendor`.                                                            |
| `content`       | JSON        |                                                                                |
| `channel`       | string      | Defaults to `"chat"`. Reserved for future modalities (`email`, `sms`, `phone`).|
| `created_at`    | timestamp   | UTC, server-default `now()`.                                                   |


### Negotiation State

A negotiation is always in exactly one of these states. <br>
The first five are active; the last four are terminal.

- **`PROSPECTING`** — Initial. The vendor has been identified as a candidate
  for this work order (via discovery + ranking) but Tavi has not yet contacted
  them.
- **`CONTACTED`** — Tavi has sent the opening pitch. Awaiting the vendor's
  first reply.
- **`NEGOTIATING`** — Active dialogue. Tavi and the vendor are exchanging
  messages to clarify scope, timing, and price. Structured facts (insurance,
  license, availability) are collected during this state.
- **`QUOTED`** — The vendor has committed to firm terms — a price and an
  available date. Awaiting a decision to accept, counter, or decline.
- **`SCHEDULED`** — Quote accepted. The job is on the calendar, awaiting
  execution.
- **`COMPLETED`** — Terminal success. The vendor performed the job
  satisfactorily.
- **`NOSHOW`** — Terminal failure *post-agreement*. The vendor failed to show
  or failed to complete after being scheduled. (Distinct from `DECLINED`
  because it indicates a trust failure, not a pre-agreement walk-away.)
- **`DECLINED`** — Terminal. Either the vendor withdrew (out of scope, too
  busy, etc.) or the offered quote was rejected without countering.
- **`CANCELLED`** — Terminal. The negotiation was killed at a non-terminal
  stage — for example, another vendor was chosen for the work order, or the
  upstream work order itself was cancelled.

### State transitions

| From                               | To          | Trigger                                          |
|------------------------------------|-------------|--------------------------------------------------|
| `PROSPECTING`                      | `CONTACTED` | Agent sends opening pitch.                       |
| `CONTACTED`                        | `NEGOTIATING` | Vendor sends first reply.                      |
| `NEGOTIATING`                      | `QUOTED`    | Agent calls `record_quote` with firm terms.      |
| `QUOTED`                           | `NEGOTIATING` | Counter-offer issued.                          |
| `QUOTED`                           | `SCHEDULED` | Quote accepted.                                  |
| `SCHEDULED`                        | `COMPLETED` | Job confirmed done.                              |
| `SCHEDULED`                        | `NOSHOW`    | Vendor failed to show / complete.                |
| `CONTACTED` / `NEGOTIATING`        | `DECLINED`  | Agent calls `close_negotiation` (vendor withdrew or out of scope). |
| `QUOTED`                           | `DECLINED`  | Quote rejected without countering.               |
| `PROSPECTING` / `CONTACTED` / `NEGOTIATING` / `QUOTED` / `SCHEDULED` | `CANCELLED` | Negotiation killed pre-execution. |

All terminal states (`COMPLETED`, `NOSHOW`, `DECLINED`, `CANCELLED`) are
write-once — no outgoing transitions. Escalation is orthogonal: the `escalated`
flag may be raised in any active state without changing `state`.

### Vendor & Vendor Communication

The vendors and the Tavi Coordinator agent interact exclusively through the negotiation_messages table, using it as their shared communication layer.<br>
For this demo, vendors are implemented as simulated LLM agents that read from and write directly to the database. As more advanced communication channels are introduced, all incoming vendor messages—regardless of source—will continue to be normalized and persisted in this same table.<br>
Likewise, the Tavi Coordinator will keep writing to negotiation_messages, while a forwarding layer can be added to relay those messages to vendors via more sophisticated external channels.

### Agent Capabilities

The Tavi Coordinator must have tools (or equivalent mechanisms) that let it do the
following during a turn.<br> Function names and signatures are left to the
implementer; only the effects matter.

**Stub policy.** Some capabilities below are *stubs* in the MVP — their real
implementations (actually sending an email, SMS, or phone call) are deferred.
A stubbed capability must still persist its message to `negotiation_messages`
with the appropriate `channel` value. When the real delivery mechanism is
added later, the DB write remains; real delivery is an additional side effect
layered on top. The DB row is the agent's conversation history and must always
exist, regardless of whether the message was also delivered via an external
channel.

| Capability                   | Effect on `negotiations` row                                      | Effect on `negotiation_messages`                                 | Allowed states |
|------------------------------|-------------------------------------------------------------------|-------------------------------------------------------------------|----------------|
| **Send an email**            | `PROSPECTING` → `CONTACTED` on first send (any channel). Otherwise no state change. Updates `updated_at`. | Appends a row with `sender = tavi`, `channel = email`. *Stub:* DB-only today; real SMTP deferred. | `PROSPECTING`, `NEGOTIATING`, `QUOTED` |
| **Send a text (SMS)**        | Same.                                                             | Appends a row with `sender = tavi`, `channel = sms`. *Stub:* DB-only today; real SMS gateway deferred. | `PROSPECTING`, `NEGOTIATING`, `QUOTED` |
| **Initiate a phone call**    | Same.                                                             | Appends a row with `sender = tavi`, `channel = phone`, content summarizing the intended call. *Stub:* DB-only today; real Vapi deferred. | `PROSPECTING`, `NEGOTIATING`, `QUOTED` |
| **Record a firm quote**      | Sets `quoted_price_cents`, `quoted_available_at`, and transitions state `NEGOTIATING → QUOTED`. | Typically paired with an outbound message acknowledging the quote, but the capability itself is a pure DB write. | `NEGOTIATING` |
| **Record extracted facts**   | Sets `insurance_verified`, `license_verified`, and/or merges keys into `attributes` (e.g., license number, insurance carrier, availability notes). No state change. | None. | any active |
| **Escalate**                 | Sets `escalated = true`; writes reason into `attributes.escalation_reason`. No state change. | None. | any active |
| **Close the negotiation**    | `CONTACTED` or `NEGOTIATING` → `DECLINED`; writes reason into `attributes.terminal_reason`. | Optional polite decline message. | `CONTACTED`, `NEGOTIATING` |
| **Act on a quote: accept**   | `QUOTED → SCHEDULED`.                                             | Typically paired with an acceptance message.                       | `QUOTED` |
| **Act on a quote: counter**  | `QUOTED → NEGOTIATING`. Counter terms appear in the outbound message. | Must append a counter-offer message.                           | `QUOTED` |
| **Act on a quote: decline**  | `QUOTED → DECLINED`; writes reason into `attributes.terminal_reason`. | Optional polite decline message.                              | `QUOTED` |

**Channel selection**

Channel preference, in order: **email → SMS (text) → phone call**. Which
channel the agent uses depends on what contact info the vendor has.

- `email` set on the vendor row → use email.
- else `phone` set → use SMS. (Assume every phone on file is SMS-capable for
  the MVP; a single `vendor.phone` field covers both SMS and voice.)
- else `phone` set and SMS fails or is not making progress → use phone call
  as a fallback.
- neither `email` nor `phone` set → the vendor cannot be contacted; filter
  this vendor out in discovery/ranking before a negotiation row is created
  for it.

In the MVP, the preferred default channel is computed per vendor by the
scheduler and passed into the agent's context as `preferred_channel`. The
agent should use that channel for every outbound message by default. The
agent may override and use a less-preferred available channel when it has a
reason (e.g., no vendor reply after several messages, or a synchronous
conversation is needed) — autonomous channel-escalation logic is out of scope
for the MVP but the capability surface supports it.

**Vendor contact info requirement.** To support channel selection, each
vendor row must carry at least `email` (nullable) and `phone` (nullable).
This is a dependency on the vendor-table spec produced in Step 2.

**Notes**

- Write-once terminal states: `COMPLETED`, `NOSHOW`, `DECLINED`, `CANCELLED`
  cannot be exited. `COMPLETED` and `NOSHOW` are reached via an external
  (operator-driven) signal, not via an agent capability.
- `CANCELLED` is set externally (by the coordinator/operator), never by the
  agent.
- The implementer is free to collapse capabilities (e.g., expose a single
  `update_attributes(**fields)` tool) or split them (e.g., separate
  `set_insurance_verified` / `set_license_verified`). What matters is that the
  effects on state and data are achievable from a single turn.

### Top Loop

This step is structured as an infinite loop.<br>
At each pass through the loop all **active** negotiations (meaning all
negotiations that are not in a terminal state) are retrieved from the
`negotiations` table.<br>
For each retrieved negotiation we determine whose turn it is — agent or vendor
— using the rule in the "Whose turn is it?" section.<br>

- If it is the **agent's turn**, we invoke the "Tavi Coordinator" agent and
  pass it the information relevant to that particular negotiation. The agent
  is described above.<br>
- If it is the **vendor's turn**, we invoke the simulated vendor agent. It
  reads the thread from `negotiation_messages` and writes back a reply row
  (`sender = vendor`). The vendor simulator is specific to this demo; in
  production, the vendor's turn is handled asynchronously — inbound email,
  SMS, or phone messages land rows in the same table without the loop
  invoking anything.

### Whose turn is it?

At each pass through the top loop, the scheduler walks every active (non-terminal)
negotiation and decides who must act next. The rule is a function of `state`
and, for `NEGOTIATING`, the last message's sender in `negotiation_messages`.

| State          | Whose turn                              | Scheduler action                                                     |
|----------------|-----------------------------------------|----------------------------------------------------------------------|
| `PROSPECTING`  | agent                                   | Invoke Tavi Coordinator. It sends the opening pitch; transitions to `CONTACTED`. |
| `CONTACTED`    | vendor                                  | Invoke vendor simulator. Its reply lands in `negotiation_messages`; transitions to `NEGOTIATING`. |
| `NEGOTIATING`  | whoever did *not* send the last message | If last sender = `vendor` → invoke Tavi Coordinator. If last sender = `tavi` → invoke vendor simulator. |
| `QUOTED`       | agent                                   | Invoke Tavi Coordinator to decide accept / counter / decline. (Cross-negotiation winner-pick logic covered in a later section.) |
| `SCHEDULED`    | neither                                 | Skip. Terminal disposition (`COMPLETED` / `NOSHOW`) comes from an external signal, not from the loop. |
| terminal       | —                                       | Skip.                                                                 |

**Notes**

- *"Last sender"* is computed with a single query:
  `SELECT sender FROM negotiation_messages WHERE negotiation_id = ? ORDER BY created_at DESC LIMIT 1`.
- A `NEGOTIATING` negotiation with no messages is an invariant violation
  (the CONTACTED → NEGOTIATING transition only fires on a vendor message insert).
- The `CONTACTED → NEGOTIATING` transition happens as a side effect of the
  vendor simulator's message insert, not via an agent tool. All other
  transitions are driven by Tavi Coordinator tool calls (see Agent Capabilities section).
- `SCHEDULED` negotiations are invisible to the loop. In the demo, the operator
  manually flips them to `COMPLETED` or `NOSHOW` via a command-center button.
