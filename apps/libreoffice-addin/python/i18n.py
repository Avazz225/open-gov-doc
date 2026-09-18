"""Minimal i18n mechanism for the LibreOffice add-in (concept 8, Phase 47
Session 5) - the first dictionary/`t()`-equivalent abstraction in this app.
Before this session, every user-facing string was a hardcoded German
literal directly in `ogdoc_addin.py`.

Mirrors the `t("area.key")` convention every frontend app's own
`I18nProvider` already uses (see `docs/services/*.md`), adapted to plain
module-level state instead of a React context: a Python-UNO script has no
component tree to hang a context provider on, the same reasoning already
established for `ogdoc_addin.py`'s own `_STATE` module dict ("Python-UNO-
Skripte werden ohnehin je Prozess nur einmal geladen").

Locale-switching strategy (ADR 0167): this add-in follows LibreOffice's own
UI-language setting automatically instead of offering an in-app switcher -
the same "follow the host" decision already made for `apps/office-addin`
(there: `Office.context.displayLanguage`), and for the same reason: a
document editor showing UI text in a different language than the
surrounding LibreOffice chrome would be actively confusing. `de` stays the
default/fallback locale if detection fails or reports an unsupported
language - existing behavior for anyone who never touches the setting.
"""

from __future__ import annotations

DEFAULT_LOCALE = "de"
_SUPPORTED_LOCALES = ("de", "en")

de = {
    "common": {
        "cancel": "Abbrechen",
        "save": "Speichern",
        "close": "Schließen",
    },
    "hub": {
        "title": "OG Doc",
        "notLoggedIn": "Nicht angemeldet.",
        "loggedInAs": "Angemeldet als {username}",
        "templatePendingNotSaved": "Vorlage geladen, noch nicht gespeichert.",
        "noDocumentLinked": "Kein Dokument verknüpft.",
        "linked": "Verknüpft: {documentId} (Version {versionNumber})",
        "btnLogin": "Anmelden...",
        "btnSaveNewFromTemplate": "Als neues Dokument speichern...",
        "btnLogout": "Abmelden",
        "btnOpen": "Öffnen...",
        "btnTemplate": "Neu aus Vorlage...",
        "btnMetadata": "Metadaten...",
        "btnSave": "In OG Doc speichern",
        "btnWorkflow": "Workflow...",
        "btnUnlink": "Verknüpfung lösen",
    },
    "login": {
        "title": "Anmelden",
        "baseUrl": "Gateway-Adresse",
        "username": "Benutzername",
        "password": "Passwort",
        "submit": "Anmelden",
        "error": "Anmeldung fehlgeschlagen: {detail}",
    },
    "open": {
        "title": "Aus OG Doc öffnen",
        "query": "Suchbegriff",
        "search": "Suchen",
        "openButton": "Öffnen",
        "searchError": "Suche fehlgeschlagen: {detail}",
        "openError": "Öffnen fehlgeschlagen: {detail}",
        "pleaseSelectDocument": "Bitte ein Dokument auswählen.",
    },
    "template": {
        "title": "Neu aus Vorlage",
        "use": "Verwenden",
        "loadError": "Laden fehlgeschlagen: {detail}",
        "useError": "Vorlage laden fehlgeschlagen: {detail}",
        "pleaseSelectTemplate": "Bitte eine Vorlage auswählen.",
    },
    "metadata": {
        "title": "Metadaten",
        "titleLabel": "Titel",
        "saveError": "Speichern fehlgeschlagen: {detail}",
    },
    "saveNewFromTemplate": {
        "title": "Als neues Dokument speichern",
        "titleLabel": "Titel",
        "pleaseEnterTitle": "Bitte einen Titel eingeben.",
        "saveError": "Speichern fehlgeschlagen: {detail}",
    },
    "workflow": {
        "title": "Workflow",
        "complete": "Abschließen",
        "start": "Workflow starten",
        "pleaseSelectTask": "Bitte eine Aufgabe auswählen.",
        "completeError": "Abschließen fehlgeschlagen: {detail}",
        "pleaseSelectProcess": "Bitte einen Prozess auswählen.",
        "startError": "Start fehlgeschlagen: {detail}",
    },
}

en = {
    "common": {
        "cancel": "Cancel",
        "save": "Save",
        "close": "Close",
    },
    "hub": {
        "title": "OG Doc",
        "notLoggedIn": "Not logged in.",
        "loggedInAs": "Logged in as {username}",
        "templatePendingNotSaved": "Template loaded, not yet saved.",
        "noDocumentLinked": "No document linked.",
        "linked": "Linked: {documentId} (version {versionNumber})",
        "btnLogin": "Log in...",
        "btnSaveNewFromTemplate": "Save as new document...",
        "btnLogout": "Log out",
        "btnOpen": "Open...",
        "btnTemplate": "New from template...",
        "btnMetadata": "Metadata...",
        "btnSave": "Save to OG Doc",
        "btnWorkflow": "Workflow...",
        "btnUnlink": "Unlink",
    },
    "login": {
        "title": "Log in",
        "baseUrl": "Gateway address",
        "username": "Username",
        "password": "Password",
        "submit": "Log in",
        "error": "Login failed: {detail}",
    },
    "open": {
        "title": "Open from OG Doc",
        "query": "Search term",
        "search": "Search",
        "openButton": "Open",
        "searchError": "Search failed: {detail}",
        "openError": "Failed to open: {detail}",
        "pleaseSelectDocument": "Please select a document.",
    },
    "template": {
        "title": "New from template",
        "use": "Use",
        "loadError": "Failed to load: {detail}",
        "useError": "Failed to load template: {detail}",
        "pleaseSelectTemplate": "Please select a template.",
    },
    "metadata": {
        "title": "Metadata",
        "titleLabel": "Title",
        "saveError": "Failed to save: {detail}",
    },
    "saveNewFromTemplate": {
        "title": "Save as new document",
        "titleLabel": "Title",
        "pleaseEnterTitle": "Please enter a title.",
        "saveError": "Failed to save: {detail}",
    },
    "workflow": {
        "title": "Workflow",
        "complete": "Complete",
        "start": "Start workflow",
        "pleaseSelectTask": "Please select a task.",
        "completeError": "Failed to complete: {detail}",
        "pleaseSelectProcess": "Please select a process.",
        "startError": "Failed to start: {detail}",
    },
}

_DICTIONARIES = {"de": de, "en": en}

# Module-global instead of a parameter threaded through every dialog
# function - same "per-process singleton, no request-scoped state" reasoning
# as `ogdoc_addin._STATE`.
_active_locale = DEFAULT_LOCALE


def set_locale(locale: str) -> None:
    global _active_locale
    _active_locale = locale if locale in _SUPPORTED_LOCALES else DEFAULT_LOCALE


def get_locale() -> str:
    return _active_locale


def _resolve(dictionary: dict, path: str) -> str:
    node = dictionary
    for part in path.split("."):
        node = node[part]
    return node


def t(path: str, **variables) -> str:
    dictionary = _DICTIONARIES.get(_active_locale, de)
    try:
        text = _resolve(dictionary, path)
    except KeyError:
        # Should not happen (both dictionaries are hand-kept in sync), but
        # falls back to German rather than raising into a dialog-construction
        # call site.
        text = _resolve(de, path)
    return text.format(**variables) if variables else text


def resolve_locale_from_ui_locale(raw: str | None) -> str:
    """Maps a UNO `ooLocale` value (e.g. "en-US", "de-DE", "de") to one of
    the two dictionaries that actually exist here - anything unrecognized
    (a third language LibreOffice might report) falls back to
    `DEFAULT_LOCALE` rather than rendering an empty/broken dictionary, same
    reasoning as `apps/office-addin`'s `resolveLocaleFromDisplayLanguage`
    (ADR 0167)."""
    if not raw:
        return DEFAULT_LOCALE
    primary = raw.replace("_", "-").split("-")[0].lower()
    return primary if primary in _SUPPORTED_LOCALES else DEFAULT_LOCALE


def detect_and_set_locale_from_host(smgr, ctx) -> None:
    """Reads LibreOffice's own UI-language setting (Tools > Options >
    Language Settings > Languages > "User interface") via the standard
    `com.sun.star.configuration.ConfigurationProvider` node
    `/org.openoffice.Setup/L10N`'s `ooLocale` property, and activates the
    matching dictionary. Called once, in `open_ogdoc`, before the first
    dialog is built. Deliberately swallows any error (no working headless
    UNO script-bridge access exists in this development environment to
    verify the exact node path live against a real installation, see
    docs/services/libreoffice-addin.md "Open Points") - falling back to
    `DEFAULT_LOCALE` is strictly better than crashing the whole add-in over
    a locale-detection failure."""
    try:
        from com.sun.star.beans import PropertyValue

        provider = smgr.createInstanceWithContext(
            "com.sun.star.configuration.ConfigurationProvider", ctx
        )
        nodepath = PropertyValue()
        nodepath.Name = "nodepath"
        nodepath.Value = "/org.openoffice.Setup/L10N"
        node = provider.createInstanceWithArguments(
            "com.sun.star.configuration.ConfigurationAccess", (nodepath,)
        )
        raw_locale = node.getByName("ooLocale")
    except Exception:
        raw_locale = None
    set_locale(resolve_locale_from_ui_locale(raw_locale))
