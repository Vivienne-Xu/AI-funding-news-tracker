from pathlib import Path
from typing import Any

import pytest

from app import alert
from app.cli import main
from app.config import Settings
from app.send import SendError

SETTINGS = Settings(gemini_api_key="gem-secret-key-123", resend_api_key="re_secret_key_456", email_from="a@example.com", email_to="b@example.com")


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    mails: list[dict[str, str]] = []
    monkeypatch.setattr(alert, "send_email", lambda settings, subject, html, text: mails.append({"subject": subject, "html": html, "text": text}))
    return mails


def test_alert_contains_the_job_link_and_last_log_lines(tmp_path: Path, sent: list[dict[str, str]]) -> None:
    log = tmp_path / "run.log"
    log.write_text("\n".join(f"line {n}" for n in range(100)), encoding="utf-8")
    subject = alert.send_failure_alert(SETTINGS, "daily", "https://github.com/x/y/actions/runs/1", log)
    (mail,) = sent
    assert subject == mail["subject"] == "AI Funding: the daily run failed"
    assert "line 99" in mail["text"] and "line 75\n" in mail["text"] and "line 74\n" not in mail["text"]  # last 25 lines only
    assert "https://github.com/x/y/actions/runs/1" in mail["text"] and 'href="https://github.com/x/y/actions/runs/1"' in mail["html"]


def test_secrets_never_appear_in_the_alert(tmp_path: Path, sent: list[dict[str, str]]) -> None:
    log = tmp_path / "run.log"
    log.write_text("failed with key gem-secret-key-123 and re_secret_key_456", encoding="utf-8")
    alert.send_failure_alert(SETTINGS, "daily", "", log)
    mail = sent[0]
    assert "gem-secret-key-123" not in mail["text"] + mail["html"] and "re_secret_key_456" not in mail["text"] + mail["html"]
    assert "[hidden]" in mail["text"]


def test_log_text_is_escaped_in_the_html(tmp_path: Path, sent: list[dict[str, str]]) -> None:
    log = tmp_path / "run.log"
    log.write_text("<script>alert(1)</script>", encoding="utf-8")
    alert.send_failure_alert(SETTINGS, "daily", "", log)
    assert "<script>" not in sent[0]["html"] and "&lt;script&gt;" in sent[0]["html"]


def test_missing_log_still_sends(sent: list[dict[str, str]]) -> None:
    alert.send_failure_alert(SETTINGS, "monthly", "", None)
    assert "no log file" in sent[0]["text"] and "No log link" in sent[0]["text"]


def test_cli_alert_reports_a_send_problem_instead_of_crashing(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise SendError("refused")

    monkeypatch.setattr(alert, "send_email", refuse)
    assert main(["alert", "--job", "daily"]) == 1
    assert "Could not send the alert" in capsys.readouterr().out
