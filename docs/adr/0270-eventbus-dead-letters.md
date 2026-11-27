# 0270 — Bounded delivery and dead letters on the event bus

**Status:** accepted (P99-S1 delivery limit and DLQ; P99-S2 listing, replay, discard)
**Context:** a consumer whose handler raises didn't ack. JetStream redelivered after `ack_wait`, and with
the servers' unlimited `max_deliver` it did so forever. A message that can never succeed (a
foreign-key violation, a malformed payload, a bug) stalled the consumer behind it:
- Phase 51: a folder's `resource_node` delete, "dangling forever";
- P98-S1: role assignments on deleted folders, over 4,500 failures in 5 minutes, new folders
  unregistered, so `403` everywhere downstream.

Both times nothing told an operator; it took a person reading logs.

## Decision

1. **Retry with a growing delay.** On a handler failure `dms-eventbus-client` `nak`s with 1, 2, 4 … s,
   at most 5 minutes (`retry_delay`). A dependency that is restarting or briefly down gets about 18
   minutes, and the consumer isn't hammered meanwhile.
2. **Dead-letter after the last attempt.** At `num_delivered >= max_deliver` (default 12,
   `DMS_EVENTBUS_MAX_DELIVER`, or per client) the message goes to the `DLQ` stream and is then
   acknowledged:
   - subject `dlq.<stream>.<consumer>`;
   - the original payload and headers;
   - plus `Dms-Dlq-Subject`, `-Stream`, `-Stream-Seq`, `-Consumer`, `-Deliveries`, `-Error` (type and
     message, 500 characters), `-Failed-At`.

   If even the DLQ can't take it, the message is `nak`ed with the longest delay: nothing is dropped.
   The DLQ keeps 90 days (`DMS_EVENTBUS_DLQ_MAX_AGE_DAYS`).
3. **Enforced in the client, not the server.** The consumer config keeps `max_deliver = -1`.
   - The client decides from `num_delivered`, so it always sees the final attempt and can store the
     message with its error.
   - A server-side limit would silently stop delivering, without a copy.
   - Existing durables need no migration (unlike ADR 0264).
4. **Visible.**
   - monitoring-service exposes `eventbus_dead_letters{stream,consumer}`, read from the DLQ on every
     scrape and alert tick. An empty DLQ is an explicit 0.
   - The default package's rule „Unzustellbare Ereignisse" warns from the first letter.
   - The alert goes to the operations mailbox like every other.

Rejected:
- A server-side `max_deliver` with an advisory listener: two moving parts, and the message itself
  isn't kept.
- Per-service DLQ tables: every service would need its own store and UI. One stream fits the bus that
  already exists.

## Decision 5 — Replay goes to the one consumer that failed (P99-S2)

Re-publishing a letter on its original subject would hand it to every consumer of that subject again,
including those that processed it long ago. Most handlers aren't idempotent.

So every durable subscription also gets a replay channel:
- subject `replay.<stream>.<consumer>`, durable `<consumer>-replay`, the same handler;
- in the `REPLAY` stream: work-queue retention, so a replayed message is gone once acked; 7 days;
- best effort: a service still starts if the channel can't be set up;
- a replay that fails again is filed under the original stream and consumer (not `…-replay`), with its
  original subject and sequence and `Dms-Replay-Of`. It shows up where it was, marked.

**Operations** (monitoring-service, `admin.monitoring`, since payloads can hold personal data):
- `GET /dead-letters` (newest first) and `GET /dead-letters/{seq}` (the event, cut at 64 KB);
- `POST /dead-letters/{seq}/replay` with a reason;
- `DELETE /dead-letters/{seq}?reason=` (final).

Both actions are audited (`monitoring.dead_letter.replayed`/`.discarded`). The original event stays
findable in the audit trail by subject and sequence. CLI `dms events dead-letters …`, admin UI section
„Unzustellbare Ereignisse", skill recipe d5.

## Consequences

- Same behaviour for sequential and concurrent (`max_concurrency > 1`) handlers.
- A poison message no longer blocks; it waits, visible, in the DLQ. Replay or discard comes in
  P99-S2 (CLI, admin UI, audit). Until then letters can be read from the stream directly.
- The change takes effect in each service with its next image build (every service uses the library).
- **Tests:**
  - dms-eventbus-client: a poison message dead-lettered with its headers while the next message flows;
    a transient failure recovering; the concurrent path; the delay series;
  - monitoring-service: the metric with a letter, and the zero.
- **Live (P99-S1):** with the default rule imported, an artificial letter turned it to `warning`
  (value 1) at the next evaluation; with the DLQ empty it reads `ok` (0).
- **Live (P99-S2)**, on the rebuilt stack with permission-service at `DMS_EVENTBUS_MAX_DELIVER=3`:
  - a `folder.resource.moved` to a parent that doesn't exist failed at 12:59:44, retried after 1 s and
    2 s, and was dead-lettered at 12:59:47 (`dlq.folder.permission-service`);
  - the rule went to `warning`;
  - `dms events dead-letters show` named the missing parent;
  - after creating the parent, `replay` reached permission-service only, the node moved, the DLQ was
    empty, the rule went back to `ok`, and the audit trail holds the replay with its reason.
- **Test hygiene:** the library tests now remove their `test_*` streams and replay consumers
  (conftest). 67 leftover test streams were removed from the dev stack.
