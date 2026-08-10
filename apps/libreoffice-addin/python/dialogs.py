"""Programmatisch erzeugte UNO-Dialoge (P14-S9) - kein `.xdl`-Ressourcen-
Layout, jeder Dialog wird direkt über die AWT-Dialog-API zusammengebaut
(`UnoControlDialogModel` + Kindsteuerelemente). Vermeidet eine zusätzliche
Ressourcendatei pro Dialog, hält die gesamte UI-Logik in lesbarem Python.

Ersetzt bei apps/office-addin (P14-S8) den web-basierten Taskpane - UNOs
Dialog-Modell kennt kein Äquivalent zu einem dauerhaft angedockten Seiten-
panel ohne deutlich schwereres Engineering (eine eigene Sidebar-Deck-
Implementierung, praktisch nur in Java/C++ üblich) - "soweit die jeweilige
Plattform es zulässt" (Konzept 3.3a) wird hier bewusst als "ein Hub-Dialog
mit Buttons, die weitere fokussierte Dialoge öffnen" umgesetzt, siehe
ADR 0046.
"""

from __future__ import annotations


def create_dialog_model(smgr, ctx, *, title: str, width: int, height: int):
    model = smgr.createInstanceWithContext("com.sun.star.awt.UnoControlDialogModel", ctx)
    model.PositionX = 100
    model.PositionY = 100
    model.Width = width
    model.Height = height
    model.Title = title
    return model


def add_control(model, control_type: str, name: str, *, x, y, width, height, **props):
    control_model = model.createInstance(f"com.sun.star.awt.UnoControl{control_type}Model")
    control_model.PositionX = x
    control_model.PositionY = y
    control_model.Width = width
    control_model.Height = height
    for key, value in props.items():
        setattr(control_model, key, value)
    model.insertByName(name, control_model)
    return control_model


def add_label(model, name, *, x, y, width, height=10, label=""):
    return add_control(model, "FixedText", name, x=x, y=y, width=width, height=height, Label=label)


def add_edit(model, name, *, x, y, width, height=12, text="", password=False):
    props = {"Text": text}
    if password:
        props["EchoChar"] = ord("*")
    return add_control(model, "Edit", name, x=x, y=y, width=width, height=height, **props)


def add_button(model, name, *, x, y, width=50, height=14, label=""):
    return add_control(model, "Button", name, x=x, y=y, width=width, height=height, Label=label)


def add_list_box(model, name, *, x, y, width, height=40, items=()):
    box = add_control(model, "ListBox", name, x=x, y=y, width=width, height=height, Dropdown=False)
    if items:
        box.StringItemList = tuple(items)
    return box


def show_dialog(smgr, ctx, model, *, parent=None):
    """Erzeugt das eigentliche, sichtbare Steuerelement aus dem Modell und
    liefert es zurück (noch nicht ausgeführt) - `dialog.execute()` blockiert,
    bis der Dialog geschlossen wird (modale Aktions-Dialoge, siehe
    Modul-Docstring). Status-/Fehlermeldungen werden bewusst als Text in
    einem Label INNERHALB des Dialogs gesetzt statt über eine separate
    native Message-Box-API - identisches Prinzip wie das `error-text`/
    `hint`-Element in apps/office-addin (P14-S8), keine zusätzliche,
    schwerer zu verifizierende AWT-API-Fläche."""
    dialog = smgr.createInstanceWithContext("com.sun.star.awt.UnoControlDialog", ctx)
    dialog.setModel(model)
    toolkit = smgr.createInstanceWithContext("com.sun.star.awt.Toolkit", ctx)
    dialog.createPeer(toolkit, parent)
    return dialog


def set_status(dialog, control_name: str, text: str) -> None:
    dialog.getControl(control_name).setText(text)
