# 0249 — Changing running processes, and what a BPMN lane means (scoping)

**Status:** proposed (scoping, no code)
**Context:** P90-S7 (Phase 90, user-selected from the comparison with an external reference system).

Two related gaps in workflow-service:

1. **A running instance can't be changed.** A circulation folder (Umlaufmappe, a case with its process
   instance) follows the route its BPMN model fixed when it started. In practice routes change while a
   folder circulates:
   - another co-signer is needed;
   - one drops out (left, on leave, no longer responsible);
   - the folder has to go back to the author.

   Today the only way out is to cancel and start again, which loses the history in the instance. The
   external reference system lets the initiator change a circulation's remaining route.
2. **BPMN lanes mean nothing.** `task_spec.lane` is parsed and shown, never enforced (workflow-service
   "Open Points", ADR 0211). That blocks two things:
   - reviewer-ui can't pre-select "meine Aufgaben" (every principal sees the same full list);
   - routing by role doesn't exist.

   ADR 0145 deferred lane resolution and chose instance-creator attribution instead.

## What exists to build on

- **SpiffWorkflow 3.1.2** (checked in the installed package):
  - `BpmnWorkflow.reset_from_task_id(task_id, data)` sends an instance back to an already completed task.
    Descendants are removed, including subprocesses and boundary-event branches.
  - `bpmn.util.diff.SpecDiff`/`WorkflowDiff`/`migrate_workflow` align an instance with a changed spec.
    They report removed and changed task specs and re-predict READY/WAITING tasks.
  - A READY task can be completed programmatically, which is how `complete` already works.
  - There is no API to inject a task into a running instance without a changed spec.
- **workflow-service:**
  - the full serialized state per instance (ADR 0019);
  - optimistic concurrency on instances (ADR 0185);
  - versioned definitions (ADR 0027): an instance stays on the version it started with;
  - claims, org-hierarchy grants and delegation (ADR 0121/0145/0211).
- **permission-service:** groups with members (and org units, ADR 0143), roles, `is_supervisor_of`.
- **process-designer:** properties providers for task extensions (signature tasks, federated steps).

## Part A — Changing running instances

### Options

- **(a) Free spec surgery per instance.** Edit the instance's model in the designer, save an
  instance-specific revision, migrate the instance with `migrate_workflow`.
  - Fully general, but the alignment can fail in ways that are hard to explain: a removed READY task, a
    gateway whose branches changed, parallel tokens, timers.
  - Every edit is a new model nobody reviewed (ADR 0087's import review gate exists for a reason).
  - Hard to test and hard to undo.
- **(b) Circulation routes as data.** A shipped "Umlauf" process template loops over a route held in the
  instance data. Each step is one entry, `{kind: mitzeichnen|zeichnen|kenntnis, assignee: principal or
  group, note}`, with a cursor.
  - Adding, removing or reordering *future* entries is a data change. Sending the folder back means
    inserting the author (or any earlier assignee) right after the cursor.
  - The engine never sees anything unusual: one user task, a script task that advances the cursor, an
    exclusive gateway "weitere Schritte?".
- **(c) A small set of engine operations for any process,** using only the supported APIs:
  - **skip** a READY user task: complete it administratively, marked as skipped, with a reason;
  - **send back** to a completed user task: `reset_from_task_id`;
  - **move to a newer definition version:** `migrate_workflow`, only after a dry run whose diff
    aligns every READY/WAITING task, otherwise refused with the report.

### Decision: (b) for circulation folders, (c) for everything else, not (a)

**(b) is the actual need.** "Mitzeichner hinzufügen", "Schritt streichen" and "an Verfasser
zurück" are what users do with a circulation. As data they are safe, explainable, and visible as a
list in the UI.
- Existing circulation definitions keep working unchanged. The template is an addition, offered when
  a case is created.
- The route can be edited by:
  - the case's initiator (instance `created_by`);
  - holders of `case.write` on the case (ADR 0144);
  - supervisors of the initiator.
- **Already completed steps can't be changed.** The current step can only be recalled (its claim is
  released, and so are its org-hierarchy grants), then the folder is redirected.

**(c) covers processes modelled freely in BPMN,** where a step must be skipped or redone. It is an admin
operation, not something a process participant does.

**Rules for both:**
- **Permission:**
  - (b) needs the case rights above;
  - (c) needs a new capability `workflow.instance_modify` (seeded in a new admin role), separate from
    the definition admin `admin.object_config`.
- **Always with a reason.** Optimistic concurrency per ADR 0185, so two people changing one instance
  conflict cleanly (`409`). Refused during maintenance mode.
- **Never skippable:**
  - a signature task: a required signature isn't an admin's to waive;
  - a waiting federated step: the remote instance would be orphaned;
  - a task inside a running multi-instance or parallel branch: there is no clean single-task semantics
    there. That case is refused with a reason.
- **Claims:** removed or skipped tasks lose their claims, and the grants are revoked through the
  existing release path.
- **Audit and notification:** each change publishes `workflow.instance.modified` with the operation,
  task before/after, the reason and the actor (in the audit ledger). The claimant of a removed task and,
  for (b), a newly inserted assignee are notified.
- **"Jump forward to any later step" is deliberately missing.** In BPMN that means skipping everything
  in between, including gateways that would have decided differently. For (b) it is "Schritte
  streichen"; for (c) repeated skips with a reason each.

## Part B — Lanes

### Options

1. **Display only (status quo),** plus a purely cosmetic filter in reviewer-ui. Without membership
   there is nothing to filter on.
2. **Lane = authorization boundary for every lane.** This breaks existing models: their lane names are
   free text and were never meant as roles.
3. **Lanes become an authorization boundary when bound.** A lane may name a group (or org unit) or a
   role. A bound lane restricts who can claim and complete its tasks; an unbound lane behaves as today.

### Decision: 3, opt-in binding

**Where the binding lives:** in the BPMN, as an extension on the lane (`dms:group` or `dms:role`, set in
a new lane properties panel in process-designer). It travels with export, import and versions.
workflow-service reads it at upload into a `lane_bindings` column (lane id → binding). The import review
gate (ADR 0087) shows the bindings, and an unknown group or role is a `422`.

**What a binding enforces:**
- **Claim and complete:** the principal must be a member (group/org unit) or hold the role. With
  delegation (ADR 0211), the represented principal is checked, not the deputy.
- **Supervisors** of a member may reassign, as today. Who receives a reassigned task must be eligible
  too.
- **Unbound lanes and models without lanes:** unchanged (claims only, ADR 0211).

**What it gives the UI:**
- `GET /tasks?eligible=me` returns tasks the caller may work on: in their lanes, claimed by them, or in
  unbound lanes. Each task carries `eligible: bool`.
- reviewer-ui preselects "Meine Aufgaben" with it; the full list stays one click away for those with
  `workflow.read`.
- **Visibility is not restricted.** The list has always been open to `workflow.read`, and hiding
  tasks is a separate decision with an information-leak dimension (like P91-S4 for folders). Recorded
  as a candidate.

**Routing:** `workflow.task.escalated` already carries the lane. With a binding, notification-service
can notify the lane's members instead of only `escalationEmail`. That is a small follow-up in the build.

**Circulation template (Part A, b):** its single user task has no lane. Each route entry names its
assignee (a principal or a group) directly, and the same eligibility check applies per entry. Lanes and
routes use one membership check.

## Proposed build (not scheduled)

| Session | Content |
|---|---|
| 1 | Lanes: the extension in the BPMN and a lane properties panel in process-designer; `lane_bindings` at upload with review-gate display and `422` for unknown bindings; eligibility on claim/complete/reassign; `GET /tasks?eligible=me` and the `eligible` flag; reviewer-ui "Meine Aufgaben"; escalation to lane members. Tests with a two-lane model. |
| 2 | Engine operations (Part A, c): skip, send back, migrate with a dry-run diff; capability `workflow.instance_modify`; guards (signature, federated, multi-instance/parallel); claims released; `workflow.instance.modified` to audit; admin-ui instance detail with the three actions and the diff report. |
| 3 | Circulation template (Part A, b): the shipped template; route data and cursor; route endpoints (add, remove, reorder future steps, recall and redirect); user-ui "Umlaufmappen" route editor; notifications to inserted assignees; e2e: add a co-signer mid-circulation, remove one, send back to the author. |

## Not in scope

- Free per-instance model surgery (option a).
- Hiding tasks a principal isn't eligible for (a candidate, see above).
- Bulk migration of all running instances of a definition to a new version. The dry run per instance
  is the base; a bulk run is a follow-up when a real migration needs it.
- Lanes in DMN or in federated steps.

## Consequences if built

- **workflow-service:**
  - a `lane_bindings` column, an eligibility check that calls permission-service (group membership,
    roles);
  - new instance-operation and route endpoints, a new capability and an event;
  - the circulation template shipped as a seeded definition.
- **process-designer:** a lane properties panel.
- **reviewer-ui:** the "Meine Aufgaben" filter. **admin-ui:** instance operations. **user-ui:** the
  route editor.
- **notification-service:** lane-member escalation, plus route-change notices.
