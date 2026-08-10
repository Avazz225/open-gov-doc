"""OG Doc LibreOffice-Writer-Erweiterung (Konzept 3.3a, P14-S9).

Einstiegspunkt `open_ogdoc` ist an den Menüeintrag "Extras > OG Doc
öffnen..." gebunden (Addons.xcu) und zeigt einen "Hub"-Dialog mit Buttons
für jede Aktion - derselbe Ein-Knopf-öffnet-alles-Gedanke wie
apps/office-addin (P14-S8), nur als Dialog-Kette statt eines dauerhaften
Web-Taskpanes (UNO kennt kein leichtgewichtiges Äquivalent dazu, siehe
dialogs.py-Modul-Docstring und ADR 0046).

Reine Geschäftslogik (Statustexte, Feld->Attribut-Übersetzung, Dateiendung-
Erkennung) ist bewusst in eigene, UNO-unabhängige Funktionen ausgelagert
(Präfix keiner, siehe unten) - testbar mit reinem `unittest` ohne echten
UNO-Kontext. Alles, was `XSCRIPTCONTEXT`/`smgr`/`ctx` direkt anfasst, ist NUR
gegen einen echten oder gemockten UNO-Kontext lauffähig (siehe tests/).
"""

from __future__ import annotations

import mimetypes
import tempfile
import uuid
from pathlib import Path

import dialogs
import dms_client
import settings_store
import unohelper
from com.sun.star.awt import XActionListener

# Der Ordnername der zentralen Vorlagenbibliothek - identische Konvention
# wie apps/office-addin (NEXT_PUBLIC_TEMPLATE_LIBRARY_FOLDER_NAME, P14-S8),
# hier ohne Build-Zeit-Konfiguration (kein Build-Schritt für ein .oxt) fest
# vorbelegt; über die Konstante unten leicht anpassbar.
TEMPLATE_LIBRARY_FOLDER_NAME = "Vorlagen"

# Zustand über mehrere Dialoge einer "Sitzung" hinweg (ein `open_ogdoc`-
# Aufruf) - bewusst Modul-globaler Zustand statt eines UNO-Service-Objekts,
# da Python-UNO-Skripte ohnehin je Prozess nur einmal geladen werden.
_STATE = {"working_doc": None, "session": None}


# --- Reine Logik (kein UNO) - unabhängig testbar --------------------------


def hub_status_text(
    session: dict | None,
    linked: settings_store.LinkedDocument | None,
    has_pending_template: bool = False,
) -> str:
    if session is None:
        return "Nicht angemeldet."
    who = f"Angemeldet als {session['username']}"
    if has_pending_template:
        return f"{who}\nVorlage geladen, noch nicht gespeichert."
    if linked is None:
        return f"{who}\nKein Dokument verknüpft."
    return f"{who}\nVerknüpft: {linked.document_id} (Version {linked.version_number})"


def attributes_from_field_values(schema_attribute_names, field_values: dict) -> dict:
    """`field_values` sind rohe Dialogfeld-Texte (immer `str`) - reduziert
    auf genau die im Objekttyp-Schema bekannten Attributnamen, leere Werte
    werden weggelassen (identisches Verhalten wie ein leeres Textfeld im
    Web-Taskpane, das beim Speichern nicht mitgeschickt wird)."""
    return {
        name: field_values[name]
        for name in schema_attribute_names
        if field_values.get(name)
    }


def guess_file_extension(content_type: str | None, fallback: str = ".odt") -> str:
    if not content_type:
        return fallback
    ext = mimetypes.guess_extension(content_type.split(";")[0].strip())
    return ext or fallback


def local_content_type_for_extension(path: str) -> str:
    content_type, _ = mimetypes.guess_type(path)
    return content_type or "application/octet-stream"


# --- UNO-Hilfsfunktionen --------------------------------------------------


def _ctx():
    return XSCRIPTCONTEXT.getComponentContext()  # noqa: F821 - von LO injiziert


def _smgr(ctx):
    return ctx.ServiceManager


def _desktop(smgr, ctx):
    return smgr.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)


def _mk_prop(name, value):
    from com.sun.star.beans import PropertyValue

    prop = PropertyValue()
    prop.Name = name
    prop.Value = value
    return prop


def _working_document():
    return _STATE["working_doc"] or XSCRIPTCONTEXT.getDocument()  # noqa: F821


def _session() -> dict | None:
    if _STATE["session"] is None:
        _STATE["session"] = settings_store.load_session()
    return _STATE["session"]


def _base_url() -> str:
    session = _session()
    return session["base_url"] if session else "http://localhost:8009"


def _token() -> str | None:
    session = _session()
    return session["token"] if session else None


class _ActionListener(unohelper.Base, XActionListener):
    def __init__(self, callback):
        self._callback = callback

    def actionPerformed(self, _event):
        self._callback()

    def disposing(self, _event):
        pass


def _load_into_new_window(smgr, ctx, *, file_bytes: bytes, extension: str, as_template: bool):
    """Öffnet heruntergeladene Dokumentbytes als NEUES LibreOffice-Fenster
    (`loadComponentFromURL`) - das UNO-Äquivalent zu Word-JS'
    `insertFileFromBase64(..., replace)`, aber idiomatischer für ein
    Desktop-Programm: ein echtes "Datei öffnen" statt eines In-Place-
    Ersatzes im bereits offenen Fenster. `AsTemplate=True` (nur beim
    Vorlagen-Import) nutzt LibreOffice' eigene "Neues Dokument aus
    Vorlage"-Semantik - ein Vorteil gegenüber Word, das keine vergleichbare
    Lade-Option kennt, siehe ADR 0046."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="ogdoc_"))
    tmp_path = tmp_dir / f"{uuid.uuid4().hex}{extension}"
    tmp_path.write_bytes(file_bytes)
    url = unohelper.systemPathToFileUrl(str(tmp_path))

    desktop = _desktop(smgr, ctx)
    props = [_mk_prop("Hidden", False)]
    if as_template:
        props.append(_mk_prop("AsTemplate", True))
    return desktop.loadComponentFromURL(url, "_blank", 0, tuple(props))


def _document_bytes(doc, *, content_type: str) -> bytes:
    """Exportiert den AKTUELLEN Bearbeitungsstand in eine temporäre Datei
    (`storeToURL`, unabhängig davon, ob der Nutzer die Datei bereits lokal
    gespeichert hat) und liest sie zurück - das UNO-Äquivalent zu Office.js'
    `getFileAsync`, hier über direkten Dateisystemzugriff sogar robuster
    (kein Slice-Zusammenbau nötig)."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="ogdoc_save_"))
    extension = guess_file_extension(content_type)
    tmp_path = tmp_dir / f"{uuid.uuid4().hex}{extension}"
    url = unohelper.systemPathToFileUrl(str(tmp_path))
    filter_name = "MS Word 2007 XML" if extension == ".docx" else "writer8"
    doc.storeToURL(url, (_mk_prop("FilterName", filter_name),))
    return tmp_path.read_bytes()


# --- Einstiegspunkt (Addons.xcu) ------------------------------------------


def open_ogdoc(*_args):
    _STATE["working_doc"] = XSCRIPTCONTEXT.getDocument()  # noqa: F821
    _run_hub_loop()


def _run_hub_loop():
    while True:
        action = _show_hub_dialog()
        if action is None or action == "close":
            return
        _ACTIONS[action]()


# --- Hub-Dialog ------------------------------------------------------------


def _show_hub_dialog() -> str | None:
    ctx = _ctx()
    smgr = _smgr(ctx)
    session = _session()
    doc = _working_document()
    linked = settings_store.get_linked_document(doc) if session else None
    pending_template = _STATE.get("pending_template")

    model = dialogs.create_dialog_model(smgr, ctx, title="OG Doc", width=220, height=40)
    dialogs.add_label(model, "lblStatus", x=10, y=8, width=200, height=24,
                       label=hub_status_text(session, linked, pending_template is not None),
                       MultiLine=True)

    buttons = _hub_buttons(session, linked, pending_template is not None)
    y = 36
    for name, label in buttons:
        dialogs.add_button(model, name, x=10, y=y, width=200, label=label)
        y += 16
    dialogs.add_button(model, "btnClose", x=10, y=y, width=200, label="Schließen")
    model.Height = y + 18

    dialog = dialogs.show_dialog(smgr, ctx, model)
    chosen = {"action": None}

    def make_handler(action_name):
        def handler():
            chosen["action"] = action_name
            dialog.endExecute()

        return handler

    listeners = []
    for name, _label in buttons + [("btnClose", "")]:
        action_name = "close" if name == "btnClose" else name
        listener = _ActionListener(make_handler(action_name))
        listeners.append(listener)
        dialog.getControl(name).addActionListener(listener)

    dialog.execute()
    dialog.dispose()
    return chosen["action"]


def _hub_buttons(session, linked, has_pending_template: bool = False):
    if session is None:
        return [("btnLogin", "Anmelden...")]
    if has_pending_template:
        return [
            ("btnSaveNewFromTemplate", "Als neues Dokument speichern..."),
            ("btnLogout", "Abmelden"),
        ]
    if linked is None:
        return [
            ("btnOpen", "Öffnen..."),
            ("btnTemplate", "Neu aus Vorlage..."),
            ("btnLogout", "Abmelden"),
        ]
    return [
        ("btnMetadata", "Metadaten..."),
        ("btnSave", "In OG Doc speichern"),
        ("btnWorkflow", "Workflow..."),
        ("btnUnlink", "Verknüpfung lösen"),
        ("btnLogout", "Abmelden"),
    ]


# --- Anmelden ----------------------------------------------------------


def _handle_login():
    ctx = _ctx()
    smgr = _smgr(ctx)
    model = dialogs.create_dialog_model(smgr, ctx, title="Anmelden", width=200, height=110)
    dialogs.add_label(model, "lblBaseUrl", x=10, y=8, width=180, label="Gateway-Adresse")
    dialogs.add_edit(model, "edBaseUrl", x=10, y=18, width=180, text=_base_url())
    dialogs.add_label(model, "lblUsername", x=10, y=34, width=180, label="Benutzername")
    dialogs.add_edit(model, "edUsername", x=10, y=44, width=180)
    dialogs.add_label(model, "lblPassword", x=10, y=60, width=180, label="Passwort")
    dialogs.add_edit(model, "edPassword", x=10, y=70, width=180, password=True)
    dialogs.add_label(model, "lblError", x=10, y=86, width=180, label="")
    dialogs.add_button(model, "btnLogin", x=10, y=98, width=85, label="Anmelden")
    dialogs.add_button(model, "btnCancel", x=105, y=98, width=85, label="Abbrechen")

    dialog = dialogs.show_dialog(smgr, ctx, model)

    def do_login():
        base_url = dialog.getControl("edBaseUrl").getText()
        username = dialog.getControl("edUsername").getText()
        password = dialog.getControl("edPassword").getText()
        try:
            token_response = dms_client.login(base_url, username, password)
        except dms_client.ApiError as exc:
            dialogs.set_status(dialog, "lblError", f"Anmeldung fehlgeschlagen: {exc.message}")
            return
        settings_store.save_session(
            base_url=base_url, token=token_response["access_token"], username=username
        )
        _STATE["session"] = {"base_url": base_url, "token": token_response["access_token"],
                              "username": username}
        dialog.endExecute()

    dialog.getControl("btnLogin").addActionListener(_ActionListener(do_login))
    dialog.getControl("btnCancel").addActionListener(_ActionListener(dialog.endExecute))
    dialog.execute()
    dialog.dispose()


def _handle_logout():
    settings_store.clear_session()
    _STATE["session"] = None


# --- Öffnen ------------------------------------------------------------


def _handle_open():
    ctx = _ctx()
    smgr = _smgr(ctx)
    token = _token()
    base_url = _base_url()

    model = dialogs.create_dialog_model(smgr, ctx, title="Aus OG Doc öffnen", width=220, height=140)
    dialogs.add_label(model, "lblQuery", x=10, y=8, width=200, label="Suchbegriff")
    dialogs.add_edit(model, "edQuery", x=10, y=18, width=150)
    dialogs.add_button(model, "btnSearch", x=162, y=18, width=48, height=12, label="Suchen")
    dialogs.add_list_box(model, "lstResults", x=10, y=34, width=200, height=80)
    dialogs.add_label(model, "lblError", x=10, y=116, width=200, label="")
    dialogs.add_button(model, "btnOpenSelected", x=10, y=128, width=95, label="Öffnen")
    dialogs.add_button(model, "btnCancel", x=115, y=128, width=95, label="Abbrechen")

    dialog = dialogs.show_dialog(smgr, ctx, model)
    state = {"results": []}

    def do_search():
        query = dialog.getControl("edQuery").getText()
        try:
            state["results"] = dms_client.search_documents(base_url, token, query)
        except dms_client.ApiError as exc:
            dialogs.set_status(dialog, "lblError", f"Suche fehlgeschlagen: {exc.message}")
            return
        listbox = dialog.getControl("lstResults")
        listbox.Model.StringItemList = tuple(r["title"] for r in state["results"])

    def do_open():
        listbox = dialog.getControl("lstResults")
        index = listbox.getSelectedItemPos()
        if index < 0 or index >= len(state["results"]):
            dialogs.set_status(dialog, "lblError", "Bitte ein Dokument auswählen.")
            return
        document = state["results"][index]
        try:
            _open_document_by_id(smgr, ctx, document["id"])
        except dms_client.ApiError as exc:
            dialogs.set_status(dialog, "lblError", f"Öffnen fehlgeschlagen: {exc.message}")
            return
        dialog.endExecute()

    dialog.getControl("btnSearch").addActionListener(_ActionListener(do_search))
    dialog.getControl("btnOpenSelected").addActionListener(_ActionListener(do_open))
    dialog.getControl("btnCancel").addActionListener(_ActionListener(dialog.endExecute))
    dialog.execute()
    dialog.dispose()


def _open_document_by_id(smgr, ctx, document_id: str):
    token, base_url = _token(), _base_url()
    detail = dms_client.get_document(base_url, token, document_id)
    content, content_type = dms_client.download_document_content(base_url, token, document_id)
    new_doc = _load_into_new_window(smgr, ctx, file_bytes=content,
                                     extension=guess_file_extension(content_type),
                                     as_template=False)
    settings_store.set_linked_document(new_doc, document_id, detail["current_version_number"],
                                        content_type)
    try:
        session_id = str(uuid.uuid4())
        dms_client.acquire_lock(base_url, token, document_id,
                                 locked_by=_session()["username"], session_id=session_id)
    except dms_client.ApiError:
        pass  # Sperre bereits von jemand anderem gehalten - schreibgeschützt weiter öffnen
    _STATE["working_doc"] = new_doc


# --- Neu aus Vorlage -----------------------------------------------------


def _handle_template():
    ctx = _ctx()
    smgr = _smgr(ctx)
    token, base_url = _token(), _base_url()

    model = dialogs.create_dialog_model(smgr, ctx, title="Neu aus Vorlage", width=220, height=120)
    dialogs.add_list_box(model, "lstTemplates", x=10, y=8, width=200, height=80)
    dialogs.add_label(model, "lblError", x=10, y=94, width=200, label="")
    dialogs.add_button(model, "btnUse", x=10, y=104, width=95, label="Verwenden")
    dialogs.add_button(model, "btnCancel", x=115, y=104, width=95, label="Abbrechen")

    dialog = dialogs.show_dialog(smgr, ctx, model)
    state = {"templates": []}

    try:
        root_folders = dms_client.list_root_folders(base_url, token)
        folder = next(
            (f for f in root_folders if f["name"] == TEMPLATE_LIBRARY_FOLDER_NAME), None
        )
        state["templates"] = (
            dms_client.list_documents_in_folder(base_url, token, folder["id"]) if folder else []
        )
    except dms_client.ApiError as exc:
        dialogs.set_status(dialog, "lblError", f"Laden fehlgeschlagen: {exc.message}")
    dialog.getControl("lstTemplates").Model.StringItemList = tuple(
        t["title"] for t in state["templates"]
    )

    def do_use():
        listbox = dialog.getControl("lstTemplates")
        index = listbox.getSelectedItemPos()
        if index < 0 or index >= len(state["templates"]):
            dialogs.set_status(dialog, "lblError", "Bitte eine Vorlage auswählen.")
            return
        template = state["templates"][index]
        try:
            content, content_type = dms_client.download_document_content(
                base_url, token, template["id"]
            )
            new_doc = _load_into_new_window(
                smgr, ctx, file_bytes=content, extension=guess_file_extension(content_type),
                as_template=True,
            )
        except dms_client.ApiError as exc:
            dialogs.set_status(dialog, "lblError", f"Vorlage laden fehlgeschlagen: {exc.message}")
            return
        # Noch kein verknüpftes Dokument - der Zustand wird erst beim ersten
        # "In OG Doc speichern" real (siehe _handle_save), analog zu
        # apps/office-addin (P14-S8).
        _STATE["pending_template"] = {
            "template_id": template["id"],
            "template_version_number": template["current_version_number"],
            "object_type_id": template.get("object_type_id"),
            "attributes": template.get("attributes", {}),
            "content_type": content_type,
        }
        _STATE["working_doc"] = new_doc
        dialog.endExecute()

    dialog.getControl("btnUse").addActionListener(_ActionListener(do_use))
    dialog.getControl("btnCancel").addActionListener(_ActionListener(dialog.endExecute))
    dialog.execute()
    dialog.dispose()


# --- Metadaten -----------------------------------------------------------


def _handle_metadata():
    ctx = _ctx()
    smgr = _smgr(ctx)
    token, base_url = _token(), _base_url()
    doc = _working_document()
    linked = settings_store.get_linked_document(doc)
    if linked is None:
        return
    document_id = linked.document_id
    detail = dms_client.get_document(base_url, token, document_id)

    attribute_names = []
    if detail.get("object_type_id") is not None:
        object_type = dms_client.get_object_type(base_url, token, detail["object_type_id"])
        attribute_names = [a["name"] for a in object_type.get("attributes", [])]

    model = dialogs.create_dialog_model(smgr, ctx, title="Metadaten", width=220,
                                         height=40 + 14 * (len(attribute_names) + 1))
    dialogs.add_label(model, "lblTitle", x=10, y=8, width=200, label="Titel")
    dialogs.add_edit(model, "edTitle", x=10, y=18, width=200, text=detail["title"])
    y = 34
    for name in attribute_names:
        dialogs.add_label(model, f"lblAttr_{name}", x=10, y=y, width=200, label=name)
        y += 10
        dialogs.add_edit(model, f"edAttr_{name}", x=10, y=y, width=200,
                          text=str(detail.get("attributes", {}).get(name, "")))
        y += 14
    dialogs.add_label(model, "lblError", x=10, y=y, width=200, label="")
    y += 12
    dialogs.add_button(model, "btnSave", x=10, y=y, width=95, label="Speichern")
    dialogs.add_button(model, "btnCancel", x=115, y=y, width=95, label="Abbrechen")
    model.Height = y + 20

    dialog = dialogs.show_dialog(smgr, ctx, model)

    def do_save():
        title = dialog.getControl("edTitle").getText()
        field_values = {name: dialog.getControl(f"edAttr_{name}").getText()
                         for name in attribute_names}
        attributes = attributes_from_field_values(attribute_names, field_values)
        try:
            dms_client.update_document_metadata(base_url, token, document_id, title=title,
                                                 attributes=attributes)
        except dms_client.ApiError as exc:
            dialogs.set_status(dialog, "lblError", f"Speichern fehlgeschlagen: {exc.message}")
            return
        dialog.endExecute()

    dialog.getControl("btnSave").addActionListener(_ActionListener(do_save))
    dialog.getControl("btnCancel").addActionListener(_ActionListener(dialog.endExecute))
    dialog.execute()
    dialog.dispose()


# --- Speichern / Verknüpfung lösen ----------------------------------------


def _handle_save():
    """Nur für ein BEREITS verknüpftes Dokument - der "Neu aus Vorlage"-Fall
    läuft über `_handle_save_new_from_template` (sammelt zuerst einen Titel,
    siehe dort), identische Aufteilung wie apps/office-addin (P14-S8:
    `handleSaveVersion` vs. `handleSaveNewFromTemplate`)."""
    token, base_url = _token(), _base_url()
    doc = _working_document()
    linked = settings_store.get_linked_document(doc)
    if linked is None:
        return
    file_bytes = _document_bytes(doc, content_type=linked.content_type)
    filename = f"document{guess_file_extension(linked.content_type)}"
    result = dms_client.checkin_version(
        base_url, token, linked.document_id, file_bytes=file_bytes, filename=filename,
        content_type=linked.content_type, expected_base_version_number=linked.version_number,
        created_by=_session()["username"],
    )
    settings_store.set_linked_document(
        doc, linked.document_id, result["version"]["version_number"], linked.content_type
    )


def _handle_save_new_from_template():
    ctx = _ctx()
    smgr = _smgr(ctx)
    token, base_url = _token(), _base_url()
    doc = _working_document()
    pending_template = _STATE.get("pending_template")
    if pending_template is None:
        return
    attribute_names = []
    object_type_id = pending_template.get("object_type_id")
    if object_type_id is not None:
        object_type = dms_client.get_object_type(base_url, token, object_type_id)
        attribute_names = [a["name"] for a in object_type.get("attributes", [])]

    model = dialogs.create_dialog_model(smgr, ctx, title="Als neues Dokument speichern",
                                         width=220, height=40 + 14 * (len(attribute_names) + 1))
    dialogs.add_label(model, "lblTitle", x=10, y=8, width=200, label="Titel")
    dialogs.add_edit(model, "edTitle", x=10, y=18, width=200)
    y = 34
    for name in attribute_names:
        dialogs.add_label(model, f"lblAttr_{name}", x=10, y=y, width=200, label=name)
        y += 10
        dialogs.add_edit(model, f"edAttr_{name}", x=10, y=y, width=200,
                          text=str(pending_template.get("attributes", {}).get(name, "")))
        y += 14
    dialogs.add_label(model, "lblError", x=10, y=y, width=200, label="")
    y += 12
    dialogs.add_button(model, "btnSave", x=10, y=y, width=95, label="Speichern")
    dialogs.add_button(model, "btnCancel", x=115, y=y, width=95, label="Abbrechen")
    model.Height = y + 20

    dialog = dialogs.show_dialog(smgr, ctx, model)

    def do_save():
        title = dialog.getControl("edTitle").getText()
        if not title.strip():
            dialogs.set_status(dialog, "lblError", "Bitte einen Titel eingeben.")
            return
        field_values = {name: dialog.getControl(f"edAttr_{name}").getText()
                         for name in attribute_names}
        attributes = attributes_from_field_values(attribute_names, field_values)
        content_type = pending_template["content_type"]
        file_bytes = _document_bytes(doc, content_type=content_type)
        filename = f"document{guess_file_extension(content_type)}"
        try:
            created = dms_client.create_document(
                base_url, token, file_bytes=file_bytes, filename=filename,
                content_type=content_type, title=title, created_by=_session()["username"],
                object_type_id=object_type_id, attributes=attributes,
                derived_from_document_id=pending_template["template_id"],
                derived_from_version_number=pending_template["template_version_number"],
            )
        except dms_client.ApiError as exc:
            dialogs.set_status(dialog, "lblError", f"Speichern fehlgeschlagen: {exc.message}")
            return
        settings_store.set_linked_document(
            doc, created["id"], created["current_version_number"], content_type
        )
        _STATE["pending_template"] = None
        dialog.endExecute()

    dialog.getControl("btnSave").addActionListener(_ActionListener(do_save))
    dialog.getControl("btnCancel").addActionListener(_ActionListener(dialog.endExecute))
    dialog.execute()
    dialog.dispose()


def _handle_unlink():
    token, base_url = _token(), _base_url()
    doc = _working_document()
    linked = settings_store.get_linked_document(doc)
    if linked is not None:
        try:
            dms_client.release_lock(base_url, token, linked.document_id,
                                     released_by=_session()["username"])
        except dms_client.ApiError:
            pass  # Sperre ggf. bereits abgelaufen/nie erworben (Lesezugriff) - egal
        settings_store.clear_linked_document(doc)


# --- Workflow --------------------------------------------------------------


def _handle_workflow():
    ctx = _ctx()
    smgr = _smgr(ctx)
    token, base_url = _token(), _base_url()
    doc = _working_document()
    linked = settings_store.get_linked_document(doc)
    if linked is None:
        return
    document_id = linked.document_id

    instances = dms_client.list_instances_for_document(base_url, token, document_id)
    running = [i for i in instances if i["status"] == "running"]
    tasks_by_instance = {
        i["id"]: dms_client.list_instance_tasks(base_url, token, i["id"]) for i in running
    }
    task_rows = [(i["id"], t) for i in running for t in tasks_by_instance[i["id"]]]

    definitions = dms_client.list_process_definitions(base_url, token)

    model = dialogs.create_dialog_model(smgr, ctx, title="Workflow", width=220, height=150)
    dialogs.add_list_box(model, "lstTasks", x=10, y=8, width=200, height=50,
                          items=[t["name"] for _iid, t in task_rows])
    dialogs.add_button(model, "btnComplete", x=10, y=60, width=200, label="Abschließen")
    dialogs.add_list_box(model, "lstDefinitions", x=10, y=76, width=200, height=40,
                          items=[d["name"] for d in definitions])
    dialogs.add_button(model, "btnStart", x=10, y=118, width=200, label="Workflow starten")
    dialogs.add_label(model, "lblError", x=10, y=134, width=200, label="")
    dialogs.add_button(model, "btnClose", x=10, y=146, width=200, label="Schließen")
    model.Height = 168

    dialog = dialogs.show_dialog(smgr, ctx, model)

    def do_complete():
        listbox = dialog.getControl("lstTasks")
        index = listbox.getSelectedItemPos()
        if index < 0 or index >= len(task_rows):
            dialogs.set_status(dialog, "lblError", "Bitte eine Aufgabe auswählen.")
            return
        instance_id, task = task_rows[index]
        try:
            dms_client.complete_task(base_url, token, instance_id, task["id"],
                                      completed_by=_session()["username"])
        except dms_client.ApiError as exc:
            dialogs.set_status(dialog, "lblError", f"Abschließen fehlgeschlagen: {exc.message}")
            return
        dialog.endExecute()

    def do_start():
        listbox = dialog.getControl("lstDefinitions")
        index = listbox.getSelectedItemPos()
        if index < 0 or index >= len(definitions):
            dialogs.set_status(dialog, "lblError", "Bitte einen Prozess auswählen.")
            return
        definition = definitions[index]
        try:
            dms_client.start_instance(base_url, token, definition["id"],
                                       created_by=_session()["username"],
                                       business_key=document_id)
        except dms_client.ApiError as exc:
            dialogs.set_status(dialog, "lblError", f"Start fehlgeschlagen: {exc.message}")
            return
        dialog.endExecute()

    dialog.getControl("btnComplete").addActionListener(_ActionListener(do_complete))
    dialog.getControl("btnStart").addActionListener(_ActionListener(do_start))
    dialog.getControl("btnClose").addActionListener(_ActionListener(dialog.endExecute))
    dialog.execute()
    dialog.dispose()


_ACTIONS = {
    "btnLogin": _handle_login,
    "btnLogout": _handle_logout,
    "btnOpen": _handle_open,
    "btnTemplate": _handle_template,
    "btnMetadata": _handle_metadata,
    "btnSave": _handle_save,
    "btnSaveNewFromTemplate": _handle_save_new_from_template,
    "btnWorkflow": _handle_workflow,
    "btnUnlink": _handle_unlink,
}

# Von LibreOffice ausgelesen, um zu bestimmen, welche Top-Level-Funktionen
# als UNO-Skripte aufrufbar sind (`ScriptProviderForPython`-Konvention).
g_exportedScripts = (open_ogdoc,)
