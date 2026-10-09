"""The scheduled-run files can't be run here, so these tests check the mistakes that would be costly: wrong secrets, unguarded saves."""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ["daily", "monthly", "backfill"]


def load(name: str) -> dict[str, Any]:
    data = yaml.safe_load((ROOT / ".github" / "workflows" / f"{name}.yml").read_text(encoding="utf-8"))
    data["triggers"] = data.pop(True, None) or data.pop("on", None)  # YAML reads a bare `on` as True
    return data


def text(name: str) -> str:
    return (ROOT / ".github" / "workflows" / f"{name}.yml").read_text(encoding="utf-8")


def env_example_keys() -> set[str]:
    lines = (ROOT / "env.example").read_text(encoding="utf-8").splitlines()
    return {line.split("=")[0] for line in lines if "=" in line and not line.startswith("#")}


@pytest.mark.parametrize("name", WORKFLOWS)
def test_workflow_is_valid_and_shares_one_save_lock(name: str) -> None:
    wf = load(name)
    assert wf["concurrency"] == {"group": "data-branch", "cancel-in-progress": False}
    assert wf["permissions"] == {"contents": "write"} and wf["defaults"]["run"]["shell"] == "bash"
    assert "workflow_dispatch" in wf["triggers"]


@pytest.mark.parametrize("name", WORKFLOWS)
def test_only_known_secrets_are_used(name: str) -> None:
    used = set(re.findall(r"secrets\.(\w+)", text(name))) - {"GITHUB_TOKEN"}
    assert used and used <= env_example_keys()


@pytest.mark.parametrize("name", WORKFLOWS)
def test_saving_only_happens_after_a_successful_load(name: str) -> None:
    """If loading the saved data failed, saving would overwrite real data with an empty database."""
    save = next(s for wf in load(name)["jobs"].values() for s in wf["steps"] if s.get("name") == "Save data")
    assert "steps.restore.outcome == 'success'" in save["if"] and "always()" in save["if"]
    restore = next(s for wf in load(name)["jobs"].values() for s in wf["steps"] if s.get("id") == "restore")
    assert restore["run"] == "bash scripts/ci_restore_data.sh"


@pytest.mark.parametrize("name", WORKFLOWS)
def test_failure_notice_is_sent_on_failure(name: str) -> None:
    last = next(iter(load(name)["jobs"].values()))["steps"][-1]
    assert last["if"] == "failure()" and f"app alert --job {name}" in last["run"]


def test_schedules() -> None:
    assert load("daily")["triggers"]["schedule"] == [{"cron": "40 6 * * *"}, {"cron": "40 7 * * *"}]  # UK summer and winter time
    assert load("monthly")["triggers"]["schedule"] == [{"cron": "0 18 28-31 * *"}]
    assert "schedule" not in load("backfill")["triggers"]  # backfill is only ever started by hand


def test_daily_continues_only_in_the_uk_morning() -> None:
    gate = text("daily")
    assert "TZ=Europe/London date" in gate and '-ge 7 ] && [ "$hour" -le 9' in gate
    steps = next(iter(load("daily")["jobs"].values()))["steps"]
    guarded = [s for s in steps if s.get("name") in ("Install", "Load saved data", "Run the daily briefing", "Save data")]
    assert len(guarded) == 4 and all("steps.gate.outputs.go == 'true'" in s["if"] for s in guarded)


def test_monthly_continues_only_on_the_last_day_of_the_month() -> None:
    assert 'date -u -d tomorrow +%d)" = "01"' in text("monthly")


def test_scripts_exist_and_use_the_safe_settings() -> None:
    for name in ("ci_restore_data.sh", "ci_save_data.sh"):
        script = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert script.startswith("#!/usr/bin/env bash") and "set -euo pipefail" in script and "\r" not in script
    save = (ROOT / "scripts" / "ci_save_data.sh").read_text(encoding="utf-8")
    assert "git push --quiet --force" in save and "data/funding.db" in save and " data\n" in save
