from dms_connector_sdk import ConnectorDescriptor


def test_as_capability_list_is_sorted():
    descriptor = ConnectorDescriptor(
        protocol="webdav", capabilities=frozenset({"write", "read", "locking"})
    )

    assert descriptor.as_capability_list() == ["locking", "read", "write"]


def test_empty_capabilities_by_default():
    descriptor = ConnectorDescriptor(protocol="webdav")

    assert descriptor.as_capability_list() == []
