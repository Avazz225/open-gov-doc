import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))

import dms_client  # noqa: E402


def _fake_response(body: bytes, content_type: str | None = None):
    response = mock.MagicMock()
    response.read.return_value = body
    response.headers = mock.MagicMock()
    response.headers.get_content_type.return_value = content_type
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


class MultipartBuilderTests(unittest.TestCase):
    def test_includes_every_field_and_the_file_part(self):
        body, content_type = dms_client._build_multipart(
            {"title": "Vertrag", "created_by": "alice"},
            ("file", "doc.docx", "application/octet-stream", b"BYTES"),
        )
        self.assertTrue(content_type.startswith("multipart/form-data; boundary="))
        self.assertIn(b'name="title"', body)
        self.assertIn(b"Vertrag", body)
        self.assertIn(b'name="file"; filename="doc.docx"', body)
        self.assertIn(b"BYTES", body)

    def test_skips_none_valued_fields(self):
        body, _ct = dms_client._build_multipart({"comment": None, "title": "X"}, None)
        self.assertNotIn(b'name="comment"', body)
        self.assertIn(b'name="title"', body)

    def test_works_without_a_file_part(self):
        body, _ct = dms_client._build_multipart({"a": "1"}, None)
        self.assertIn(b'name="a"', body)


class RequestErrorHandlingTests(unittest.TestCase):
    @mock.patch("urllib.request.urlopen")
    def test_http_error_is_translated_to_api_error_with_detail_message(self, mock_urlopen):
        error_body = json.dumps({"detail": "Ungültige Anmeldedaten"}).encode("utf-8")
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "http://x", 401, "Unauthorized", None, mock.MagicMock(read=lambda: error_body)
        )
        with self.assertRaises(dms_client.ApiError) as ctx:
            dms_client.login("http://localhost:8009", "alice", "wrong")
        self.assertEqual(ctx.exception.status, 401)
        self.assertEqual(ctx.exception.message, "Ungültige Anmeldedaten")

    @mock.patch("urllib.request.urlopen")
    def test_successful_json_response_is_decoded(self, mock_urlopen):
        mock_urlopen.return_value = _fake_response(json.dumps({"access_token": "tok"}).encode())
        result = dms_client.login("http://localhost:8009", "alice", "secret")
        self.assertEqual(result, {"access_token": "tok"})


class DownloadContentTests(unittest.TestCase):
    @mock.patch("urllib.request.urlopen")
    def test_returns_bytes_and_content_type(self, mock_urlopen):
        mock_urlopen.return_value = _fake_response(b"raw-bytes", content_type="application/pdf")
        content, content_type = dms_client.download_document_content(
            "http://localhost:8009", "tok", "doc-1"
        )
        self.assertEqual(content, b"raw-bytes")
        self.assertEqual(content_type, "application/pdf")


if __name__ == "__main__":
    unittest.main()
