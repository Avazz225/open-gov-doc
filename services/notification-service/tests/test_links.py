from notification_service.links import build_resource_link


def test_build_resource_link_document():
    assert (
        build_resource_link("http://localhost:3000", "document", "abc-123")
        == "http://localhost:3000/?document=abc-123"
    )


def test_build_resource_link_folder():
    assert (
        build_resource_link("http://localhost:3000", "folder", "f1")
        == "http://localhost:3000/?folder=f1"
    )


def test_build_resource_link_instance():
    assert (
        build_resource_link("http://localhost:3005", "instance", "i1")
        == "http://localhost:3005/?instance=i1"
    )


def test_build_resource_link_strips_trailing_slash():
    assert (
        build_resource_link("http://localhost:3000/", "document", "abc")
        == "http://localhost:3000/?document=abc"
    )


def test_build_resource_link_none_base_url_returns_none():
    assert build_resource_link(None, "document", "abc") is None


def test_build_resource_link_quotes_resource_id():
    assert (
        build_resource_link("http://localhost:3000", "document", "a b/c")
        == "http://localhost:3000/?document=a%20b%2Fc"
    )
