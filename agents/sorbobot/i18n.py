"""Predefined messages shown to the user as they are, translated by the code (§8 of the spec)."""

import json
import logging
from collections.abc import Iterable
from pathlib import Path

logger = logging.getLogger(__name__)

LOCALES_DIR = Path(__file__).resolve().parent / "locales"

# Every key the code may display. A key missing from the default catalogue is a deployment error.
REQUIRED_KEYS = (
    "input.empty",
    "input.too_long",
    "errors.taxi_unavailable",
    "errors.graph_unavailable",
    "errors.llm_unavailable",
    "errors.llm_empty_response",
)


class TranslationError(Exception):
    """A deployment error: a catalogue is missing or invalid, or the default catalogue lacks a key."""


class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


class Translator:
    def __init__(self, catalogues: dict[str, dict[str, str]], default_language: str, supported_languages: Iterable[str]):
        self.default_language = default_language
        self.supported_languages = tuple(supported_languages)
        self._catalogues = catalogues
        missing_catalogues = [code for code in self.supported_languages if code not in catalogues]
        if missing_catalogues:
            raise TranslationError(f"No translation catalogue for language(s): {missing_catalogues}")
        default = catalogues[default_language]
        missing_keys = [key for key in REQUIRED_KEYS if not isinstance(default.get(key), str)]
        if missing_keys:
            raise TranslationError(f"Catalogue {default_language!r} lacks the key(s): {missing_keys}")

    @classmethod
    def load(cls, locales_dir: Path, default_language: str, supported_languages: Iterable[str]) -> "Translator":
        catalogues = {}
        for code in supported_languages:
            path = locales_dir / f"{code}.json"
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError as exc:
                raise TranslationError(f"Missing translation catalogue {path}") from exc
            except (OSError, ValueError) as exc:
                raise TranslationError(f"Invalid translation catalogue {path}: {exc}") from exc
            if not isinstance(data, dict):
                raise TranslationError(f"Translation catalogue {path} must hold a JSON object")
            catalogues[code] = data
        return cls(catalogues, default_language, supported_languages)

    def resolve(self, code) -> str:
        """A supported language code, or the default language when the code is unknown or unsupported."""
        if not isinstance(code, str):
            return self.default_language
        short = code.strip().lower().replace("_", "-").split("-")[0]
        return short if short in self.supported_languages else self.default_language

    def text(self, language: str, key: str, **variables) -> str:
        language = self.resolve(language)
        template = self._catalogues[language].get(key)
        if not isinstance(template, str):
            logger.warning("Translation %r missing in %r, using %r", key, language, self.default_language)
            template = self._catalogues[self.default_language].get(key)
            if not isinstance(template, str):
                raise TranslationError(f"Translation key {key!r} is missing from the default catalogue")
        # Inserted values are plain text: no format spec is interpreted on them.
        return template.format_map(_SafeDict({name: str(value) for name, value in variables.items()}))

    def missing_keys(self, language: str) -> list[str]:
        """The keys of the default catalogue that ``language`` lacks (reported by the tests, not blocking)."""
        default = self._catalogues[self.default_language]
        catalogue = self._catalogues.get(language, {})
        return [key for key in default if key not in catalogue]
