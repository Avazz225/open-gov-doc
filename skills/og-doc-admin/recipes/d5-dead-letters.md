# Undeliverable events (dead letters)

**Example:** "Was bedeutet der Alarm ‚Unzustellbare Ereignisse'?" / "Ein Dienst hat Ereignisse nicht verarbeitet"
**Role:** domain-admin-monitoring

1. `dms events dead-letters list`: consumer, original subject, attempts, error. Each one is an event a
   service gave up on after all delivery attempts (about 18 minutes of retries). The service went on
   with the rest.
2. `dms events dead-letters show <seq>`: the original event. Explain the error in plain words. Typical
   causes:
   - something the event refers to doesn't exist (yet), e.g. a parent folder;
   - a dependency was down longer than the retries;
   - a bug or a malformed event.
3. **Plan:**
   - **Replay** only once the cause is gone (the folder exists, the service is back, the fix is
     deployed): it goes to exactly that consumer, and if it fails again it comes back as a new letter.
   - **Discard** only when the event is obsolete or harmful, and only on an explicit instruction for that
     letter: it is gone for good.
4. After a yes: `dms events dead-letters replay <seq> --reason "…"` or
   `dms events dead-letters discard <seq> --reason "…"` (both audited).
5. **Verify:** `dms events dead-letters list` (the letter is gone; a failed replay shows up again,
   marked as replayed) and `dms monitoring alerts` (the rule goes back to ok at the next evaluation).

**Note:** many letters for one consumer at once usually mean one cause. Find it before replaying them
one by one.
