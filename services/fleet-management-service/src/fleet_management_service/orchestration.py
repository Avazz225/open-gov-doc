"""Pure validation/vocabulary logic for update plans and rollouts (3a
extension, P13-S2b) - no I/O here, that lives in `main.py`/`repository.py`.

Five-valued error decision (3a verbatim, "instead of binary success/
failure"): ``retry_later``/``wait_external``/``manual_required``/
``recoverable_failed``/``fatal_contract``. A simple success doesn't need its
own name - it simply means "next step", or ``status="completed"`` at the
last step."""

STEP_TYPES = frozenset({"verify", "gate"})

# The five concept values - in addition to "pending"/"completed" as the two
# structural bookend states of an `InstallationRun`.
OUTCOMES = frozenset(
    {"retry_later", "wait_external", "manual_required", "recoverable_failed", "fatal_contract"}
)

# Outcomes that an operator may explicitly provide for a "gate" step via
# `POST .../mark-done` (the person who performed the external/manual action
# knows best whether a failure is repeatable or fundamentally wrong) -
# "wait_external"/"manual_required", by contrast, are structural states, not
# an outcome reportable from outside.
REPORTABLE_GATE_OUTCOMES = frozenset(
    {"success", "retry_later", "recoverable_failed", "fatal_contract"}
)


class PlanValidationError(Exception):
    pass


def validate_steps(steps: list[dict]) -> None:
    if not steps:
        raise PlanValidationError("Ein Update-Plan braucht mindestens einen Schritt")
    for step in steps:
        if not step.get("name"):
            raise PlanValidationError("Jeder Schritt braucht einen Namen")
        if step.get("step_type") not in STEP_TYPES:
            raise PlanValidationError(
                f"Unbekannter step_type {step.get('step_type')!r} - erlaubt: {sorted(STEP_TYPES)}"
            )
