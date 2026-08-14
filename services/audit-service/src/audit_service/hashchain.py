import hashlib
import json

GENESIS_HASH = "0" * 64


def canonical_json(fields: dict) -> str:
    """Deterministic serialization - a prerequisite for the same entry
    always producing the same hash, independent of field/dict ordering.
    """
    return json.dumps(fields, sort_keys=True, default=str, ensure_ascii=False)


def compute_hash(prev_hash: str, fields: dict) -> str:
    material = f"{prev_hash}|{canonical_json(fields)}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
