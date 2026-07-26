from dms_db_base.schema import make_declarative_base


def test_schema_bound_to_metadata():
    Base = make_declarative_base("registry")
    assert Base.metadata.schema == "registry"


def test_two_bases_have_independent_metadata():
    RegistryBase = make_declarative_base("registry")
    AuditBase = make_declarative_base("audit")
    assert RegistryBase.metadata is not AuditBase.metadata
    assert RegistryBase.metadata.schema == "registry"
    assert AuditBase.metadata.schema == "audit"
