"""HTTP-Client für OG Doc, ausschließlich Python-Standardbibliothek (P14-S9).

LibreOffices gebündelter Python-Interpreter hat standardmäßig KEINE
Drittanbieter-Pakete installiert (kein `pip install requests` ohne
zusätzliche Betriebseingriffe) - `urllib.request` ist deshalb bewusst die
einzige Abhängigkeit, kein zusätzlicher Installationsschritt für Endnutzer.

Spiegelt exakt dieselben, bereits bestehenden Gateway-Endpunkte wie
apps/office-addin/src/lib/api.ts (P14-S8, 3.3a) - "gemeinsame Backend-
Schnittstelle mit dem MS-Office-Add-in" (Roadmap-Wortlaut P14-S9). Kein
neuer Backend-Code, siehe ADR 0046.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
import uuid


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _request(base_url, service_type, path, *, method="GET", token=None, json_body=None,
             form_fields=None, file_field=None):
    """`file_field`: optionales `(field_name, filename, content_type, bytes)`-Tupel
    für multipart/form-data-Uploads (Check-in/Dokument anlegen)."""
    url = f"{base_url}/api/{service_type}/{path}"
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    if file_field is not None or form_fields is not None:
        body, content_type = _build_multipart(form_fields or {}, file_field)
        headers["Content-Type"] = content_type
    elif json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    else:
        body = None

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(detail).get("detail", detail)
        except (json.JSONDecodeError, AttributeError):
            pass
        raise ApiError(exc.code, str(detail)) from exc


def _build_multipart(fields: dict, file_field):
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        if value is None:
            continue
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode("utf-8")
        )
    if file_field is not None:
        field_name, filename, content_type, data = file_field
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode("utf-8")
            + data
            + b"\r\n"
        )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


# --- Auth --------------------------------------------------------------


def login(base_url, username, password):
    return _request(base_url, "auth-service", "login", method="POST",
                     json_body={"username": username, "password": password})


def get_current_user(base_url, token):
    return _request(base_url, "auth-service", "me", token=token)


# --- Dokumente -----------------------------------------------------------


def get_document(base_url, token, document_id):
    return _request(base_url, "document-service", f"documents/{document_id}", token=token)


def download_document_content(base_url, token, document_id):
    """Liefert `(bytes, content_type)` - der Content-Type wird für die
    Wahl der richtigen Dateiendung beim lokalen Zwischenspeichern gebraucht
    (`ogdoc_addin.guess_file_extension`), und beim späteren Speichern
    wiederverwendet, damit nicht stillschweigend das Format gewechselt
    wird."""
    url = f"{base_url}/api/document-service/documents/{document_id}/content"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            content_type = resp.headers.get_content_type()
            return resp.read(), content_type
    except urllib.error.HTTPError as exc:
        raise ApiError(exc.code, exc.read().decode("utf-8", errors="replace")) from exc


def checkin_version(base_url, token, document_id, *, file_bytes, filename, content_type,
                     expected_base_version_number, created_by):
    return _request(
        base_url, "document-service", f"documents/{document_id}/versions", method="POST",
        token=token,
        form_fields={
            "expected_base_version_number": expected_base_version_number,
            "created_by": created_by,
        },
        file_field=("file", filename, content_type, file_bytes),
    )


def create_document(base_url, token, *, file_bytes, filename, content_type, title, created_by,
                     object_type_id=None, attributes=None,
                     derived_from_document_id=None, derived_from_version_number=None):
    fields = {"title": title, "created_by": created_by}
    if object_type_id is not None:
        fields["object_type_id"] = object_type_id
        fields["attributes"] = json.dumps(attributes or {})
    if derived_from_document_id is not None:
        fields["derived_from_document_id"] = derived_from_document_id
    if derived_from_version_number is not None:
        fields["derived_from_version_number"] = derived_from_version_number
    return _request(
        base_url, "document-service", "documents", method="POST", token=token,
        form_fields=fields,
        file_field=("file", filename, content_type, file_bytes),
    )


def update_document_metadata(base_url, token, document_id, *, title=None, attributes=None):
    body = {}
    if title is not None:
        body["title"] = title
    if attributes is not None:
        body["attributes"] = attributes
    return _request(base_url, "document-service", f"documents/{document_id}", method="PATCH",
                     token=token, json_body=body)


def acquire_lock(base_url, token, document_id, *, locked_by, session_id):
    return _request(base_url, "document-service", f"documents/{document_id}/lock", method="POST",
                     token=token, json_body={"locked_by": locked_by, "session_id": session_id})


def release_lock(base_url, token, document_id, *, released_by):
    return _request(base_url, "document-service", f"documents/{document_id}/lock", method="DELETE",
                     token=token, json_body={"released_by": released_by})


# --- Ordner/Vorlagenbibliothek --------------------------------------------


def list_root_folders(base_url, token):
    return _request(base_url, "folder-service", "folders/root/children", token=token)


def list_documents_in_folder(base_url, token, folder_id):
    return _request(base_url, "document-service", f"documents?folder_id={folder_id}", token=token)


# --- Objekttypen -----------------------------------------------------------


def get_object_type(base_url, token, object_type_id):
    return _request(base_url, "object-type-service", f"object-types/{object_type_id}", token=token)


# --- Suche -----------------------------------------------------------------


def search_documents(base_url, token, query):
    return _request(
        base_url, "search-service", f"search?q={urllib.parse.quote(query)}&limit=10", token=token
    )["results"]


# --- Workflow (7.1) ---------------------------------------------------------


def list_process_definitions(base_url, token):
    return _request(base_url, "workflow-service", "process-definitions", token=token)


def list_instances_for_document(base_url, token, document_id):
    return _request(base_url, "workflow-service", f"instances?business_key={document_id}",
                     token=token)


def list_instance_tasks(base_url, token, instance_id):
    return _request(base_url, "workflow-service", f"instances/{instance_id}/tasks", token=token)


def start_instance(base_url, token, process_definition_id, *, created_by, business_key):
    return _request(
        base_url, "workflow-service", f"process-definitions/{process_definition_id}/instances",
        method="POST", token=token,
        json_body={"created_by": created_by, "business_key": business_key},
    )


def complete_task(base_url, token, instance_id, task_id, *, completed_by):
    return _request(
        base_url, "workflow-service", f"instances/{instance_id}/tasks/{task_id}/complete",
        method="POST", token=token, json_body={"completed_by": completed_by, "data": {}},
    )
