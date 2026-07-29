from document_service.content_type_sniffer import sniff_content_type


def test_sniffs_plain_text():
    assert sniff_content_type(b"Hallo Welt") == "text/plain"


def test_sniffs_json_regardless_of_extension_or_header():
    assert sniff_content_type(b'{"a": 1}') == "application/json"


def test_sniffs_pdf_by_magic_bytes():
    assert sniff_content_type(b"%PDF-1.4\n...") == "application/pdf"


def test_empty_upload_falls_back_to_octet_stream():
    assert sniff_content_type(b"") == "application/octet-stream"
