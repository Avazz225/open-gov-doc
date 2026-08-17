from typing import Literal
from urllib.parse import quote

ResourceType = Literal["document", "folder", "instance"]

# Which frontend app's public base URL a resource type resolves against, and
# the query-param name used on that app's single route (Phase 29's URL
# scheme). "document" also covers "Akte" - an Akte is a document object type
# (ADR 0059), not a separate resource.
_RESOURCE_QUERY_PARAM: dict[ResourceType, str] = {
    "document": "document",
    "folder": "folder",
    "instance": "instance",
}


def build_resource_link(
    base_url: str | None, resource_type: ResourceType, resource_id: str
) -> str | None:
    if not base_url:
        return None
    param = _RESOURCE_QUERY_PARAM[resource_type]
    return f"{base_url.rstrip('/')}/?{param}={quote(resource_id, safe='')}"
