"""Smart layout generation (2.2b): derives a standard form layout (row/column
grid) from an object type's attribute list, as long as no explicit deviation
saved via the Layout Designer (P5b-S3) exists.

The same generation logic is used for all three usage purposes (display/
search/upload) - per Concept 2.2b, purpose-specific differences only arise
through individual adjustment in the Layout Designer, not through different
default heuristics.
"""

COLUMNS_PER_ROW = 2
DEFAULT_RESPONSIVE_BREAKPOINT_PX = 600


def generate_smart_layout(attributes: list[dict]) -> dict:
    """Packs attributes, in creation order, into groups of ``COLUMNS_PER_ROW``
    fields per row. ``label`` starts as a copy of the technical attribute
    name - assigning a dedicated display name is only planned for the GUI
    editor (P5b-S3); until then, the attribute list is the only available
    source. ``required`` reflects the attribute state at generation time
    (a snapshot, not a live reference - a later layout adjustment
    deliberately decouples from the object type definition, see ADR 0014)."""
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
