# Four-eyes: pending requests, switching it on or off

**Example:** "Was liegt zur Freigabe?" / "Vier-Augen für Rollenzuweisungen einschalten"
**Role:** – (list), domain-admin-config (change)

**Pending requests:** `dms approvals list`, then `dms approvals show <id>` for details. Explain who
initiated it and what it would do. **Never approve.**

**Switching on or off:**
1. `dms -o json config export --category approval_config`.
2. Write a fragment with the entry: `action_type`, `requires_approval`, optionally `required_permission`.
3. **Preview:** `scripts/preview-package.sh fragment.json`.
4. Explain the effect. Switching it off weakens a protection: get an explicit yes.
5. `dms config import fragment.json`.
6. **Verify:** export again.

**Note:** Rejecting a request (`dms approvals reject <id> --reason …`) only when the admin asks for exactly that.
