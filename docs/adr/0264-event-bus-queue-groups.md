# 0264 — Event bus: durables as queue groups

**Status:** accepted
**Context:** P95-S2a; found in P95-S2 (ADR 0257 §3a).

`dms-eventbus-client` subscribed every durable as a plain push consumer, with no deliver group. nats-py
refuses a second subscription to such a consumer: `consumer is already bound to a subscription`. So no
service with a durable could run twice:
- **Replicas:** a second permission-service container exited at startup (verified live in P95-S2).
- **Compose rollouts:** `scripts/rolling-update.sh` excluded every consumer service (recorded since P10).
- **Helm:** the default `RollingUpdate` starts a surge pod next to the old one, so `helm upgrade` of a
  consumer service hung with a crash-looping pod. The chart didn't say so. The optional `autoscaling` had
  the same problem.
- **Scaling permission-service** across processes, the lever ADR 0257 wants measured before a Rust pilot,
  was impossible.

## Decision

**Every durable subscription is a queue subscription,** with the deliver group equal to the durable
name. Every instance of a service joins the same group, and JetStream hands each message to one of them.
Ephemeral subscriptions (`durable=None`) are unchanged.

**A one-time migration at the first subscribe.** An existing durable has no deliver group and can't gain
one in place. Before subscribing, the client therefore:
- looks the durable up;
- if it has no deliver group, deletes and recreates it with one;
- keeps its filter, ack policy, ack wait, `max_deliver` and `max_ack_pending`;
- starts it at `ack_floor + 1`, the first message not yet acknowledged.

Details:
- **Nothing is lost.** Messages delivered but not yet acked come again, which is JetStream's
  at-least-once, the same as after any redelivery.
- **A durable that never delivered anything** keeps its original start rule.
- **The deliver subject is derived from the names** (`_DMS.deliver.<stream>.<durable>`), so two
  instances migrating at the same moment end up with the same consumer. A durable that already has a
  group is left alone, which makes the migration idempotent.
- **A log line** `eventbus_durable_migrated_to_queue_group` per migrated durable.

## Why not …

- **…pull consumers** (fetch in batches). That is the other standard way to share a durable. Every
  handler, the concurrency limit and the tracing span are built on push callbacks. Queue groups give the
  sharing with the same delivery model.
- **…`Recreate` as the Helm strategy for consumer services.** It would make rollouts work, but with
  downtime, and it doesn't help replicas or autoscaling.
- **…a new durable name per version.** Two consumers would each get every message, so every event would
  be handled twice.

## Verified

- **Tests (dms-eventbus-client, real NATS):**
  - two instances on one durable: 40 messages, each handled exactly once, both instances get a share;
  - an old-style durable with two of six messages acked: after the migration exactly the remaining four
    arrive, the consumer has the group, and a second subscribe joins without migrating again.
- **Live:**
  - **Migration:** all consumer services rebuilt and started. Every one migrated its durables once
    (notification-service 27, audit-service 20, the rest 1–4).
  - **Two instances:** a second permission-service container starts next to the first, and both sit in
    the queue group on four streams. 21 folder events split 10/11, none pending.
  - **Rollout:** `scripts/rolling-update.sh permission-service` ran through canary, drain and regular
    instance. The consumer-service exclusion is gone from the script.

## Consequences

- **Every consumer service can run several instances:** replicas, the Helm `RollingUpdate` and
  autoscaling. Handlers must tolerate a message arriving at a different instance than the one before,
  which they already did: state lives in the database.
- **Ordering:** with several instances, messages of one durable may be processed in parallel across
  instances. Every handler is already written for at-least-once and redelivery, and `max_concurrency > 1`
  already allowed parallelism within one instance.
- **Test runs:** `scripts/run-tests.sh` still stops a service's container before its in-process tests.
  Container and test process would now share the messages instead of conflicting.
- **A durable created with `deliver_new` that never delivered anything** is recreated with "new" at the
  migration moment. Messages published between its creation and the migration would be skipped. This only
  affects a consumer whose service never ran after creating it, which doesn't happen in a stack that has
  run once.
