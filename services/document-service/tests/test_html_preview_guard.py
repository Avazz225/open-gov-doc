from document_service.html_preview_guard import rewrite_external_references


def test_blocks_external_https_image_src():
    html = b'<img src="https://tracker.example/pixel.gif">'
    out = rewrite_external_references(html).decode("utf-8")
    assert "https://tracker.example/pixel.gif" not in out.split("Blockierte")[0]
    assert 'src="https://tracker.example/pixel.gif"' not in out
    assert "Blockierte externe Anfrage: https://tracker.example/pixel.gif" in out


def test_blocks_protocol_relative_script_src():
    html = b'<script src="//cdn.example/evil.js"></script>'
    out = rewrite_external_references(html).decode("utf-8")
    assert 'src="//cdn.example/evil.js"' not in out
    assert "Blockierte externe Anfrage: //cdn.example/evil.js" in out


def test_blocks_relative_path_reference():
    """Ein `srcDoc`-Inhalt hat keine sichere, auflösbare Basis-URL - relative
    Pfade werden daher ebenfalls blockiert, nicht nur absolute URLs."""
    html = b'<img src="images/local.png">'
    out = rewrite_external_references(html).decode("utf-8")
    assert 'src="images/local.png"' not in out
    assert "Blockierte externe Anfrage: images/local.png" in out


def test_blocks_external_link_href():
    html = b'<link rel="stylesheet" href="https://evil.example/style.css">'
    out = rewrite_external_references(html).decode("utf-8")
    assert 'href="https://evil.example/style.css"' not in out


def test_allows_data_uri():
    html = b'<img src="data:image/png;base64,AAAA" alt="ok">'
    out = rewrite_external_references(html).decode("utf-8")
    assert 'src="data:image/png;base64,AAAA"' in out
    assert "Blockierte" not in out


def test_allows_fragment_only_anchor():
    html = b'<a href="#section1">Sprungmarke</a>'
    out = rewrite_external_references(html).decode("utf-8")
    assert 'href="#section1"' in out
    assert "Blockierte" not in out


def test_allows_mailto_and_tel_links():
    html = b'<a href="mailto:test@example.com">Mail</a><a href="tel:+491234">Anruf</a>'
    out = rewrite_external_references(html).decode("utf-8")
    assert 'href="mailto:test@example.com"' in out
    assert 'href="tel:+491234"' in out
    assert "Blockierte" not in out


def test_blocks_javascript_uri():
    html = b'<a href="javascript:alert(1)">Klick</a>'
    out = rewrite_external_references(html).decode("utf-8")
    assert "javascript:alert(1)" not in out.split("Blockierte")[0]
    assert "Blockierte externe Anfrage: javascript:alert(1)" in out


def test_blocks_empty_src():
    """Ein leerer `src`-Wert lässt manche Browser die aktuelle Seite erneut
    anfordern - sicherheitshalber ebenfalls blockiert."""
    html = b'<img src="">'
    out = rewrite_external_references(html).decode("utf-8")
    assert 'src=""' not in out


def test_normalizes_meta_charset_to_utf8():
    html = b'<html><head><meta charset="iso-8859-1"></head><body></body></html>'
    out = rewrite_external_references(html).decode("utf-8")
    assert 'charset="utf-8"' in out
    assert "iso-8859-1" not in out


def test_leaves_html_without_any_references_unchanged_in_substance():
    html = b"<p>Nur Text, keine Referenzen.</p>"
    out = rewrite_external_references(html).decode("utf-8")
    assert "Nur Text, keine Referenzen." in out
    assert "Blockierte" not in out
