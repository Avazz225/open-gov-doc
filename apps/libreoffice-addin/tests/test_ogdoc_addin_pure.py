import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))

import uno_mock  # noqa: E402

uno_mock.install()

import ogdoc_addin  # noqa: E402
import settings_store  # noqa: E402


class HubStatusTextTests(unittest.TestCase):
    def test_not_logged_in(self):
        self.assertEqual(ogdoc_addin.hub_status_text(None, None), "Nicht angemeldet.")

    def test_logged_in_no_document_linked(self):
        text = ogdoc_addin.hub_status_text({"username": "alice"}, None)
        self.assertIn("alice", text)
        self.assertIn("Kein Dokument verknüpft", text)

    def test_logged_in_with_linked_document(self):
        linked = settings_store.LinkedDocument("doc-1", 3, "application/pdf")
        text = ogdoc_addin.hub_status_text({"username": "alice"}, linked)
        self.assertIn("doc-1", text)
        self.assertIn("3", text)

    def test_pending_template_takes_precedence_over_linked_state(self):
        text = ogdoc_addin.hub_status_text({"username": "alice"}, None, has_pending_template=True)
        self.assertIn("Vorlage geladen", text)


class HubButtonsTests(unittest.TestCase):
    def test_shows_only_login_when_logged_out(self):
        buttons = ogdoc_addin._hub_buttons(None, None)
        self.assertEqual([name for name, _label in buttons], ["btnLogin"])

    def test_shows_open_and_template_when_nothing_linked(self):
        buttons = ogdoc_addin._hub_buttons({"username": "alice"}, None)
        names = [name for name, _label in buttons]
        self.assertIn("btnOpen", names)
        self.assertIn("btnTemplate", names)
        self.assertNotIn("btnSave", names)

    def test_shows_document_actions_when_linked(self):
        linked = settings_store.LinkedDocument("doc-1", 1, "application/pdf")
        buttons = ogdoc_addin._hub_buttons({"username": "alice"}, linked)
        names = [name for name, _label in buttons]
        for expected in ("btnMetadata", "btnSave", "btnWorkflow", "btnUnlink"):
            self.assertIn(expected, names)

    def test_shows_only_save_new_when_pending_template(self):
        buttons = ogdoc_addin._hub_buttons({"username": "alice"}, None, has_pending_template=True)
        names = [name for name, _label in buttons]
        self.assertEqual(names, ["btnSaveNewFromTemplate", "btnLogout"])


class AttributesFromFieldValuesTests(unittest.TestCase):
    def test_keeps_only_known_schema_attributes(self):
        result = ogdoc_addin.attributes_from_field_values(
            ["kunde", "betrag"], {"kunde": "Acme", "betrag": "100", "unbekannt": "x"}
        )
        self.assertEqual(result, {"kunde": "Acme", "betrag": "100"})

    def test_drops_empty_values(self):
        result = ogdoc_addin.attributes_from_field_values(["kunde"], {"kunde": ""})
        self.assertEqual(result, {})

    def test_missing_field_is_simply_absent(self):
        result = ogdoc_addin.attributes_from_field_values(["kunde"], {})
        self.assertEqual(result, {})


class FileExtensionTests(unittest.TestCase):
    def test_docx_content_type(self):
        self.assertEqual(
            ogdoc_addin.guess_file_extension(
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
            ".docx",
        )

    def test_odt_content_type(self):
        self.assertEqual(
            ogdoc_addin.guess_file_extension("application/vnd.oasis.opendocument.text"), ".odt"
        )

    def test_unknown_or_missing_content_type_falls_back_to_odt(self):
        self.assertEqual(ogdoc_addin.guess_file_extension(None), ".odt")
        self.assertEqual(ogdoc_addin.guess_file_extension("application/x-totally-unknown"), ".odt")

    def test_strips_charset_parameter_before_guessing(self):
        self.assertEqual(ogdoc_addin.guess_file_extension("text/plain; charset=utf-8"), ".txt")


if __name__ == "__main__":
    unittest.main()
