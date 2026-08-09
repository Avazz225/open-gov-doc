"""HTTP-Client gegen eine gepaarte Ziel-Installation's `/inbound/*`-API (7.2) -
bewusst synchron, siehe `dms_client.py`s Moduldocstring für die Begründung."""

import httpx


class PeerClient:
    def __init__(self, *, base_url: str, api_key: str, timeout: float) -> None:
        self._client = httpx.Client(
            base_url=base_url, headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout
        )

    def close(self) -> None:
        self._client.close()

    def announce_transfer(
        self,
        *,
        transfer_id: str,
        source_installation_id: str,
        target_parent_folder_id: str,
        name: str,
    ) -> dict:
        response = self._client.post(
            "/inbound/transfers",
            json={
                "transfer_id": transfer_id,
                "source_installation_id": source_installation_id,
                "target_parent_folder_id": target_parent_folder_id,
                "name": name,
            },
        )
        response.raise_for_status()
        return response.json()

    def push_folder(
        self, transfer_id: str, *, parent_target_folder_id: str, name: str, created_by: str
    ) -> dict:
        response = self._client.post(
            f"/inbound/transfers/{transfer_id}/folders",
            json={
                "parent_target_folder_id": parent_target_folder_id,
                "name": name,
                "created_by": created_by,
            },
        )
        response.raise_for_status()
        return response.json()

    def push_document(
        self,
        transfer_id: str,
        *,
        target_folder_id: str,
        filename: str,
        content: bytes,
        content_type: str | None,
        created_by: str,
        existing_target_document_id: str | None,
        expected_base_version_number: int | None,
    ) -> dict:
        data = {"target_folder_id": target_folder_id, "created_by": created_by}
        if existing_target_document_id is not None:
            data["existing_target_document_id"] = existing_target_document_id
            data["expected_base_version_number"] = str(expected_base_version_number)
        response = self._client.post(
            f"/inbound/transfers/{transfer_id}/documents",
            data=data,
            files={"file": (filename, content, content_type or "application/octet-stream")},
        )
        response.raise_for_status()
        return response.json()

    def push_permission(
        self,
        transfer_id: str,
        *,
        target_resource_id: str,
        principal_type: str,
        principal_id: str,
        role_name: str,
        role_description: str,
        role_permissions: list[str],
    ) -> None:
        response = self._client.post(
            f"/inbound/transfers/{transfer_id}/permissions",
            json={
                "target_resource_id": target_resource_id,
                "principal_type": principal_type,
                "principal_id": principal_id,
                "role_name": role_name,
                "role_description": role_description,
                "role_permissions": role_permissions,
            },
        )
        response.raise_for_status()

    def verify(self, transfer_id: str, *, document_ids: list[str]) -> dict:
        response = self._client.post(
            f"/inbound/transfers/{transfer_id}/verify", json={"document_ids": document_ids}
        )
        response.raise_for_status()
        return response.json()

    def release(self, transfer_id: str) -> None:
        response = self._client.post(f"/inbound/transfers/{transfer_id}/release")
        response.raise_for_status()

    def dry_run_check(self, *, target_parent_folder_id: str) -> dict:
        response = self._client.post(
            "/inbound/transfers/dry-run-check",
            json={"target_parent_folder_id": target_parent_folder_id},
        )
        response.raise_for_status()
        return response.json()
