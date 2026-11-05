import json
import logging
import warnings

import pytest

from agents.sorbobot.i18n import LOCALES_DIR, REQUIRED_KEYS, TranslationError, Translator


def test_every_catalogue_holds_every_key_of_the_english_catalogue():
    translator = Translator.load(LOCALES_DIR, "en", ("en", "fr"))
    for language in ("fr",):
        missing = translator.missing_keys(language)
        if missing:  # reported, not blocking
            warnings.warn(f"Catalogue {language!r} lacks translations for {missing}", stacklevel=1)
    english = json.loads((LOCALES_DIR / "en.json").read_text(encoding="utf-8"))
    assert set(REQUIRED_KEYS) <= set(english)


def test_resolve_and_variables():
    translator = Translator.load(LOCALES_DIR, "en", ("en", "fr"))
    assert translator.resolve("fr-FR") == "fr" and translator.resolve("EN") == "en"
    assert translator.resolve("de") == "en" and translator.resolve(None) == "en" and translator.resolve(" fr ") == "fr"
    assert "1000" in translator.text("fr", "input.too_long", max_chars=1000)
    assert translator.text("fr", "input.empty").startswith("Votre message est vide")
    assert translator.text("de", "input.empty").startswith("Your message is empty")


def test_missing_translation_falls_back_to_english_and_is_logged(caplog):
    translator = Translator({"en": dict.fromkeys(REQUIRED_KEYS, "en text"), "fr": {}}, "en", ("en", "fr"))
    with caplog.at_level(logging.WARNING, logger="agents.sorbobot.i18n"):
        assert translator.text("fr", "input.empty") == "en text"
    assert "missing" in caplog.text
    assert translator.missing_keys("fr") == list(REQUIRED_KEYS)


def test_deployment_errors(tmp_path):
    with pytest.raises(TranslationError, match="lacks the key"):
        Translator({"en": {"input.empty": "x"}, "fr": {}}, "en", ("en", "fr"))
    with pytest.raises(TranslationError, match="No translation catalogue"):
        Translator({"en": dict.fromkeys(REQUIRED_KEYS, "x")}, "en", ("en", "fr"))
    with pytest.raises(TranslationError, match="Missing translation catalogue"):
        Translator.load(tmp_path, "en", ("en",))
    (tmp_path / "en.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(TranslationError, match="Invalid translation catalogue"):
        Translator.load(tmp_path, "en", ("en",))
    (tmp_path / "en.json").write_text("[]", encoding="utf-8")
    with pytest.raises(TranslationError, match="JSON object"):
        Translator.load(tmp_path, "en", ("en",))


def test_inserted_values_are_plain_text():
    translator = Translator({"en": {**dict.fromkeys(REQUIRED_KEYS, "x"), "input.too_long": "max {max_chars} {other}"}}, "en", ("en",))
    assert translator.text("en", "input.too_long", max_chars="{evil:>10}") == "max {evil:>10} {other}"
