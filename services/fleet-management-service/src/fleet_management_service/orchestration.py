"""Reine Validierungs-/Vokabular-Logik für Update-Pläne und Rollouts (3a-
Erweiterung, P13-S2b) - kein I/O hier, das lebt in `main.py`/`repository.py`.

Fünfwertige Fehlerentscheidung (3a wörtlich, "statt binärem Erfolg/
Fehlschlag"): ``retry_later``/``wait_external``/``manual_required``/
``recoverable_failed``/``fatal_contract``. Ein einfacher Erfolg braucht
keinen eigenen Namen - er bedeutet schlicht "nächster Schritt" bzw.
``status="completed"`` beim letzten Schritt."""

STEP_TYPES = frozenset({"verify", "gate"})

# Die fünf Konzept-Werte - zusätzlich zu "pending"/"completed" als die beiden
# strukturellen Bookend-Zustände eines `InstallationRun`.
OUTCOMES = frozenset(
    {"retry_later", "wait_external", "manual_required", "recoverable_failed", "fatal_contract"}
)

# Outcomes, die ein Bediener einem "gate"-Schritt explizit über
# `POST .../mark-done` mitgeben darf (die Person, die die externe/manuelle
# Aktion durchgeführt hat, weiß am besten, ob ein Fehlschlag wiederholbar
# oder grundsätzlich falsch ist) - "wait_external"/"manual_required" sind
# dagegen strukturelle Zustände, kein von außen meldbares Ergebnis.
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
