"""Loads settings from `.env` and the YAML files in `config/`."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"


class Settings(BaseModel):
    gemini_api_key: str = ""
    llm_model_fast: str = "gemini-3.5-flash-lite"
    llm_model_writer: str = "gemini-3.5-flash-lite"  # monthly commentary: one call a month (tested on the free tier)
    max_daily_llm_usd: float = 0.25
    max_monthly_llm_usd: float = 3.00
    resend_api_key: str = ""
    email_from: str = ""
    email_to: str = ""  # one address, or several separated by commas
    send_quiet_day_note: bool = True  # on a day with no deals: send a short note (true) or nothing (false)
    db_path: Path = Path("data/funding.db")


class Source(BaseModel):
    name: str
    type: Literal["rss", "html_list", "edgar"]
    url: str
    tier: int = Field(ge=1, le=3)
    region: Literal["US", "Europe", "Asia", "RoW"]
    enabled: bool = True
    needs_ai_match: bool = False
    bare_dollar: Literal["USD", "CAD"] = "USD"  # what a plain "$" means in this source


def read_env_file(path: Path) -> dict[str, str]:
    """Reads simple KEY=value lines. Ignores blank lines and # comments."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_settings(env_file: Path | None = None) -> Settings:
    """Real environment variables win over the `.env` file. Empty values are ignored."""
    file_values = read_env_file(env_file or PROJECT_ROOT / ".env")
    merged = {**file_values, **os.environ}
    wanted = Settings.model_fields.keys()
    chosen = {k: merged[k.upper()] for k in wanted if merged.get(k.upper())}
    settings = Settings(**chosen)
    if not settings.db_path.is_absolute():
        settings.db_path = PROJECT_ROOT / settings.db_path
    return settings


def load_yaml(name: str, config_dir: Path | None = None) -> dict[str, Any]:
    path = (config_dir or CONFIG_DIR) / name
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping at the top level")
    return data


def load_sources(config_dir: Path | None = None) -> list[Source]:
    data = load_yaml("sources.yaml", config_dir)
    return [Source(**item) for item in data.get("sources") or []]


def load_regions(config_dir: Path | None = None) -> list[str]:
    data = load_yaml("regions.yaml", config_dir)
    return [item["code"] for item in data["regions"]]


def load_country_regions(config_dir: Path | None = None) -> dict[str, str]:
    """Returns {country name in lower case: region code}."""
    data = load_yaml("regions.yaml", config_dir)
    valid = {item["code"] for item in data["regions"]}
    result: dict[str, str] = {}
    for region, names in data["countries"].items():
        if region not in valid:
            raise ValueError(f"regions.yaml: unknown region '{region}' in countries")
        result.update({str(name).casefold(): region for name in names})
    return result


def load_fx_rates(config_dir: Path | None = None) -> dict[str, float]:
    """Returns {currency code: US dollars per unit}."""
    rates = load_yaml("fx_rates.yaml", config_dir)["rates"]
    return {code: float(rate) for code, rate in rates.items()}


def load_categories(config_dir: Path | None = None) -> dict[str, str]:
    """Returns {category code: layer}."""
    data = load_yaml("categories.yaml", config_dir)
    layers = set(data["layers"])
    result: dict[str, str] = {}
    for item in data["categories"]:
        if item["layer"] not in layers:
            raise ValueError(f"Category {item['code']} has unknown layer {item['layer']}")
        result[item["code"]] = item["layer"]
    return result


def load_investor_tiers(config_dir: Path | None = None) -> dict[str, list[str]]:
    data = load_yaml("investor_tiers.yaml", config_dir)
    return {
        "tier_1": [n.lower() for n in data.get("tier_1") or []],
        "tier_2": [n.lower() for n in data.get("tier_2") or []],
    }


def load_aliases(config_dir: Path | None = None) -> dict[str, str]:
    data = load_yaml("company_aliases.yaml", config_dir)
    return {k.lower(): v for k, v in (data.get("aliases") or {}).items()}
