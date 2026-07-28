"""Smart-Layout-Generierung (2.2b): leitet ein Standard-Formular-Layout
(Zeilen/Spalten-Grid) aus der Attributliste eines Objekttyps ab, solange keine
über den Layout-Designer (P5b-S3) explizit gespeicherte Abweichung existiert.

Dieselbe Generierung wird für alle drei Verwendungszwecke (Anzeige/Suche/
Upload) verwendet - Zweck-spezifische Unterschiede entstehen laut Konzept 2.2b
erst durch individuelles Nachjustieren im Layout-Designer, nicht durch
unterschiedliche Default-Heuristiken.
"""

COLUMNS_PER_ROW = 2
DEFAULT_RESPONSIVE_BREAKPOINT_PX = 600


def generate_smart_layout(attributes: list[dict]) -> dict:
    """Packt Attribute in Anlage-Reihenfolge zu je ``COLUMNS_PER_ROW`` Feldern
    pro Zeile. ``label`` startet als Kopie des technischen Attributnamens -
    eine eigene Anzeigename-Vergabe ist erst mit dem GUI-Editor (P5b-S3)
    vorgesehen; bis dahin ist die Attributliste die einzige verfügbare
    Quelle. ``required`` spiegelt den Attribut-Stand zum Generierungszeitpunkt
    (Snapshot, keine live Referenz - eine spätere Layout-Anpassung entkoppelt
    sich bewusst von der Objekttyp-Definition, siehe ADR 0014)."""
    rows = []
    for start in range(0, len(attributes), COLUMNS_PER_ROW):
        chunk = attributes[start : start + COLUMNS_PER_ROW]
        rows.append(
            {
                "columns": [
                    {
                        "attribute": attribute["name"],
                        "label": attribute["name"],
                        "required": bool(attribute.get("required", False)),
                    }
                    for attribute in chunk
                ]
            }
        )
    return {"rows": rows, "responsive_breakpoint_px": DEFAULT_RESPONSIVE_BREAKPOINT_PX}
