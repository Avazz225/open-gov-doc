import hashlib
import json

GENESIS_HASH = "0" * 64


def canonical_json(fields: dict) -> str:
    """Deterministische Serialisierung - Voraussetzung dafür, dass derselbe
    Eintrag immer denselben Hash erzeugt, unabhängig von Feld-/Dict-Reihenfolge.
    """
    return json.dumps(fields, sort_keys=True, default=str, ensure_ascii=False)


def compute_hash(prev_hash: str, fields: dict) -> str:
    material = f"{prev_hash}|{canonical_json(fields)}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
