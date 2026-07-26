from audit_service.hashchain import GENESIS_HASH, compute_hash


def test_same_input_produces_same_hash():
    fields = {"a": 1, "b": "x"}
    assert compute_hash(GENESIS_HASH, fields) == compute_hash(GENESIS_HASH, fields)


def test_field_order_does_not_matter():
    a = compute_hash(GENESIS_HASH, {"a": 1, "b": 2})
    b = compute_hash(GENESIS_HASH, {"b": 2, "a": 1})
    assert a == b


def test_different_prev_hash_changes_result():
    fields = {"a": 1}
    assert compute_hash(GENESIS_HASH, fields) != compute_hash("f" * 64, fields)


def test_different_content_changes_result():
    assert compute_hash(GENESIS_HASH, {"a": 1}) != compute_hash(GENESIS_HASH, {"a": 2})
