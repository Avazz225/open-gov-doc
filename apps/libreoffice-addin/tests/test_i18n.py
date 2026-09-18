import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))

import uno_mock  # noqa: E402

uno_mock.install()

import i18n  # noqa: E402


class FakeConfigNode:
    def __init__(self, ui_locale):
        self._ui_locale = ui_locale

    def getByName(self, name):
        assert name == "ooLocale"
        return self._ui_locale


class FakeConfigProvider:
    def __init__(self, ui_locale):
        self._ui_locale = ui_locale

    def createInstanceWithArguments(self, service, _args):
        assert service == "com.sun.star.configuration.ConfigurationAccess"
        return FakeConfigNode(self._ui_locale)


class FakeServiceManager:
    """Fake enough of `smgr.createInstanceWithContext` to exercise
    `detect_and_set_locale_from_host` without a real UNO context - same
    "no working headless UNO script-bridge access" constraint as every
    other test in this app (see uno_mock.py's own module docstring)."""

    def __init__(self, ui_locale=None, raise_error=False):
        self._ui_locale = ui_locale
        self._raise_error = raise_error

    def createInstanceWithContext(self, service, _ctx):
        assert service == "com.sun.star.configuration.ConfigurationProvider"
        if self._raise_error:
            raise RuntimeError("boom")
        return FakeConfigProvider(self._ui_locale)


class TranslationLookupTests(unittest.TestCase):
    def tearDown(self):
        i18n.set_locale(i18n.DEFAULT_LOCALE)

    def test_defaults_to_german(self):
        self.assertEqual(i18n.get_locale(), "de")
        self.assertEqual(i18n.t("hub.notLoggedIn"), "Nicht angemeldet.")

    def test_switches_to_english(self):
        i18n.set_locale("en")
        self.assertEqual(i18n.t("hub.notLoggedIn"), "Not logged in.")

    def test_unsupported_locale_falls_back_to_default(self):
        i18n.set_locale("fr")
        self.assertEqual(i18n.get_locale(), "de")

    def test_formats_placeholders_in_both_locales(self):
        self.assertEqual(i18n.t("hub.loggedInAs", username="alice"), "Angemeldet als alice")
        i18n.set_locale("en")
        self.assertEqual(i18n.t("hub.loggedInAs", username="alice"), "Logged in as alice")

    def test_dictionaries_have_the_same_keys(self):
        def flatten(d, prefix=""):
            keys = set()
            for key, value in d.items():
                path = f"{prefix}.{key}" if prefix else key
                if isinstance(value, dict):
                    keys |= flatten(value, path)
                else:
                    keys.add(path)
            return keys

        self.assertEqual(flatten(i18n.de), flatten(i18n.en))


class ResolveLocaleFromUiLocaleTests(unittest.TestCase):
    def test_german_variants(self):
        self.assertEqual(i18n.resolve_locale_from_ui_locale("de-DE"), "de")
        self.assertEqual(i18n.resolve_locale_from_ui_locale("de"), "de")

    def test_english_variants(self):
        self.assertEqual(i18n.resolve_locale_from_ui_locale("en-US"), "en")
        self.assertEqual(i18n.resolve_locale_from_ui_locale("en_GB"), "en")

    def test_unsupported_language_falls_back_to_default(self):
        self.assertEqual(i18n.resolve_locale_from_ui_locale("fr-FR"), "de")

    def test_missing_value_falls_back_to_default(self):
        self.assertEqual(i18n.resolve_locale_from_ui_locale(None), "de")
        self.assertEqual(i18n.resolve_locale_from_ui_locale(""), "de")


class DetectAndSetLocaleFromHostTests(unittest.TestCase):
    def tearDown(self):
        i18n.set_locale(i18n.DEFAULT_LOCALE)

    def test_activates_english_when_host_reports_it(self):
        i18n.detect_and_set_locale_from_host(FakeServiceManager(ui_locale="en-US"), ctx=None)
        self.assertEqual(i18n.get_locale(), "en")

    def test_keeps_default_when_host_reports_german(self):
        i18n.detect_and_set_locale_from_host(FakeServiceManager(ui_locale="de-DE"), ctx=None)
        self.assertEqual(i18n.get_locale(), "de")

    def test_falls_back_to_default_on_any_error(self):
        # No working headless UNO script-bridge access exists in this
        # environment to verify the real node path against a live
        # installation (see docs/services/libreoffice-addin.md "Open
        # Points") - detection must degrade gracefully, not crash the
        # add-in, if the configuration lookup ever fails.
        i18n.detect_and_set_locale_from_host(FakeServiceManager(raise_error=True), ctx=None)
        self.assertEqual(i18n.get_locale(), "de")


if __name__ == "__main__":
    unittest.main()
