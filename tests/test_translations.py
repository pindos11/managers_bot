from progress_bot.translations import Translator


def test_english_translation_file_is_loaded() -> None:
    translator = Translator("en")
    assert translator.text("target_set", day="2026-09-07", units=12) == "Target for 2026-09-07 set to 12."


def test_missing_language_and_key_fall_back_safely() -> None:
    translator = Translator("does-not-exist")
    assert translator.text("none") == "none"
    assert translator.text("missing_key") == "missing_key"
