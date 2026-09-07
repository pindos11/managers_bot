from __future__ import annotations

import json
import logging
import re
from pathlib import Path


LOGGER = logging.getLogger(__name__)
LANGUAGE_PATTERN = re.compile(r"^[a-z0-9_-]+$", re.IGNORECASE)


class Translator:
    """Loads message templates from JSON, with English as a safe fallback."""

    def __init__(self, language: str = "en") -> None:
        self.language = language.lower()
        self._english = self._load("en")
        selected = self._load(self.language) if self.language != "en" else {}
        self.messages = self._english | selected

    @staticmethod
    def _path(language: str) -> Path | None:
        if not LANGUAGE_PATTERN.fullmatch(language):
            return None
        return Path(__file__).with_name("translations") / f"{language}.json"

    def _load(self, language: str) -> dict[str, str]:
        path = self._path(language)
        if path is None or not path.is_file():
            if language != "en":
                LOGGER.warning("Translation file for language=%s was not found; using English", language)
            return {}
        try:
            content = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"Invalid translation file {path}: {error}") from error
        if not isinstance(content, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in content.items()):
            raise ValueError(f"Translation file {path} must be a JSON object of string keys and values")
        return content

    def text(self, key: str, **values: object) -> str:
        template = self.messages.get(key) or self._english.get(key) or key
        try:
            return template.format(**values)
        except (KeyError, ValueError):
            LOGGER.exception("Invalid values for translation key=%s", key)
            return self._english.get(key, key)
