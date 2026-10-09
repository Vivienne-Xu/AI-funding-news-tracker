from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import (
    Source,
    load_aliases,
    load_categories,
    load_investor_tiers,
    load_regions,
    load_settings,
    load_sources,
    read_env_file,
)


def test_read_env_file_ignores_comments_and_quotes(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text('# note\n\nA=1\nB="two"\nC=\n', encoding="utf-8")
    assert read_env_file(env) == {"A": "1", "B": "two", "C": ""}


def test_read_env_file_missing_file_is_empty(tmp_path: Path) -> None:
    assert read_env_file(tmp_path / "nope") == {}


def test_load_settings_uses_defaults_and_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAX_DAILY_LLM_USD", raising=False)
    monkeypatch.delenv("LLM_MODEL_FAST", raising=False)
    env = tmp_path / ".env"
    env.write_text("MAX_DAILY_LLM_USD=0.10\nLLM_MODEL_FAST=\n", encoding="utf-8")
    settings = load_settings(env)
    assert settings.max_daily_llm_usd == 0.10
    assert settings.llm_model_fast == "gemini-3.5-flash-lite"  # empty value ignored


def test_real_environment_beats_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    env.write_text("MAX_DAILY_LLM_USD=0.10\n", encoding="utf-8")
    monkeypatch.setenv("MAX_DAILY_LLM_USD", "0.50")
    assert load_settings(env).max_daily_llm_usd == 0.50


def test_shipped_config_files_load() -> None:
    assert load_regions() == ["US", "Europe", "Asia", "RoW"]
    categories = load_categories()
    assert categories["robotics"] == "Physical AI"
    assert set(load_investor_tiers()) == {"tier_1", "tier_2"}
    assert isinstance(load_aliases(), dict)
    sources = load_sources()
    assert len(sources) >= 10
    assert len({s.name for s in sources}) == len(sources)  # no duplicate names
    assert {s.region for s in sources} == {"US", "Europe", "Asia", "RoW"}


def test_source_rejects_bad_values() -> None:
    good = {"name": "X", "type": "rss", "url": "https://x.test/feed", "tier": 1, "region": "US"}
    assert Source(**good).enabled is True
    with pytest.raises(ValidationError):
        Source(**{**good, "tier": 4})
    with pytest.raises(ValidationError):
        Source(**{**good, "region": "Mars"})
    with pytest.raises(ValidationError):
        Source(**{**good, "type": "twitter"})
