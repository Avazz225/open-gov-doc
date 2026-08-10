import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))

import uno_mock  # noqa: E402

uno_mock.install()

import settings_store  # noqa: E402


class SessionPersistenceTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_session_file = settings_store.SESSION_FILE
        settings_store.SESSION_FILE = Path(self._tmp_dir.name) / "session.json"

    def tearDown(self):
        settings_store.SESSION_FILE = self._original_session_file
        self._tmp_dir.cleanup()

    def test_no_session_by_default(self):
        self.assertIsNone(settings_store.load_session())

    def test_save_and_load_roundtrip(self):
        settings_store.save_session(
            base_url="http://localhost:8009", token="tok-1", username="alice"
        )
        session = settings_store.load_session()
        self.assertEqual(
            session,
            {
                "base_url": "http://localhost:8009",
                "token": "tok-1",
                "username": "alice",
            },
        )

    def test_clear_removes_the_file(self):
        settings_store.save_session(
            base_url="http://localhost:8009", token="tok-1", username="alice"
        )
        settings_store.clear_session()
        self.assertIsNone(settings_store.load_session())

    def test_clear_without_existing_session_is_a_noop(self):
        settings_store.clear_session()  # darf nicht werfen


class DocumentLinkingTests(unittest.TestCase):
    def setUp(self):
        self.doc = uno_mock.FakeDocument()

    def test_unlinked_document_returns_none(self):
        self.assertIsNone(settings_store.get_linked_document(self.doc))

    def test_set_then_get_roundtrip(self):
        settings_store.set_linked_document(
            self.doc, "doc-1", 3, "application/vnd.oasis.opendocument.text"
        )
        linked = settings_store.get_linked_document(self.doc)
        self.assertEqual(linked.document_id, "doc-1")
        self.assertEqual(linked.version_number, 3)
        self.assertEqual(linked.content_type, "application/vnd.oasis.opendocument.text")

    def test_set_twice_overwrites_rather_than_duplicating(self):
        settings_store.set_linked_document(self.doc, "doc-1", 1, "text/plain")
        settings_store.set_linked_document(self.doc, "doc-1", 2, "text/plain")
        linked = settings_store.get_linked_document(self.doc)
        self.assertEqual(linked.version_number, 2)

    def test_clear_removes_the_linkage(self):
        settings_store.set_linked_document(self.doc, "doc-1", 1, "text/plain")
        settings_store.clear_linked_document(self.doc)
        self.assertIsNone(settings_store.get_linked_document(self.doc))

    def test_clear_without_existing_linkage_is_a_noop(self):
        settings_store.clear_linked_document(self.doc)  # darf nicht werfen


if __name__ == "__main__":
    unittest.main()
