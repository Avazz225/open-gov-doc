# 0247 — Approver notification: who is told about a new four-eyes request

**Status:** accepted
**Context:** P90-S3 (Phase 90, user-selected from the comparison with an external reference system).

permission-service has published `permission.approval.requested` since the generic four-eyes mechanism
(4.3) was built. Nothing consumed it. A request waited until someone happened to open the approval inbox
(reviewer-ui, user-ui's "Freigaben"). The initiator heard back on approval or rejection (P71-S1), but
nobody heard that there was something to decide.

## Decision 1 — Resolve "who may decide" in permission-service, in three steps

Who may decide is governed by the action type's `ApprovalActionConfig`:
- **With `required_permission`** (break-glass: `breakglass.approve`), exactly its holders may decide, and
  they are the natural recipients.
- **Without it,** Konzept 4.3's rule applies: "any second person". Telling every account would be noise,
  not a notification. In the eGov package this is the case for almost every gated action type.

So permission-service, which knows roles, assignments and groups, resolves recipients in this order:

1. **`recipient_role`:** new, optional per action type and set on the approval settings page. Its
   holders are notified, for example "Registratur" for `document.force_delete`. It only decides who is
   told, not who may decide.
2. **The holders of `required_permission`.**
3. **The holders of `approval.notify`,** the new seeded role "approval-recipients"
   ("Freigabe-Benachrichtigung"). `ensure_domain_admin_roles` creates it on existing installations too.
   An admin grants it to the people who should hear about any request. Holding it grants no right to
   decide.

**"Holders"** means user principals with the role at the root resource, directly or through a real
group's membership:
- The implicit "everyone" group is skipped (that would be every account).
- Service principals are skipped (machines don't read notifications).
- The initiator is always excluded: they can't approve their own request.
- The list is capped at 50, and the cap is reported (`recipients_truncated`).

**When it is resolved.** At request time, and the list travels in the event (`recipients`,
`recipient_source`, `recipients_truncated`). The message then goes to whoever held the role when the
request was made, and the audit trail shows who was told.

`GET /approval-config/{type}/recipients` shows the same resolution for the settings page. It is gated
like changing the configuration.

## Decision 2 — notification-service delivers in-app and by mail

notification-service consumes `permission.approval.requested` (its fourth subject on the `permission`
stream, own durable name).
- **Directory lookup:** it maps the principal ids to user names and addresses through auth-service's
  `/users/service-directory`.
- **Per recipient:** an in-app message under the user name, and a mail when the account has an address.
- **Ids without an entry:** technical accounts, which the directory leaves out, get the in-app message
  under their id. A failed lookup sends in-app only.
- **Text:** the template use case `permission.approval.requested` (placeholders `action_type`,
  `initiated_by`, `request_id`, `link`, `recipient_name`) with a German fallback. The link points to the
  reviewer-ui's approval inbox (`/approvals/`), where requests are decided.
- **Known cosmetic gap:** a request made by a technical account names the initiator by id ("2"), since
  the directory has no entry for it.

## Decision 3 — `PUT /approval-config` changes only what is sent

**The bug found on the way.** `PUT /approval-config/{type}` overwrote `required_permission` with `null`
whenever the body didn't contain it. admin-ui knew and always resent it. config-service's import didn't:
- a package entry `{action_type, requires_approval}` cleared an installation's `required_permission`;
- for `auth.superuser.activate` that would silently drop the break-glass group rule.

**The fix:**
- `required_permission` and `recipient_role` are now changed only when sent (`model_fields_set`).
- An unknown `recipient_role` is `422`.
- config-service's `approval_config` category now exports both fields. On import it sends them only when
  the entry contains them, so a package that only flips `requires_approval` leaves them alone, and an
  export/import round trip keeps them.

## Consequences

- **permission-service:** `recipient_role` column, `resolve_approval_recipients`, the preview endpoint,
  the seeded role, and the recipients in the event.
- **notification-service:** consumer, template and directory lookup.
- **config-service:** the two fields in `approval_config`.
- **admin-ui approval settings:** a "Benachrichtigt" role choice per action type, and "Wer?" showing the
  source and the names.

**Not in scope:**
- Reminders for requests nobody has decided (escalation): a candidate for the timer pattern of P90-S1.
- Notifying deputies (delegations).
- Per-action-type mail wording beyond the template.
