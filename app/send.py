"""Step 10: send an email through the Resend web service."""

from __future__ import annotations

from typing import Any

import httpx

from app.collect import USER_AGENT
from app.config import Settings

RESEND_URL = "https://api.resend.com/emails"
MAX_RECIPIENTS = 50  # Resend's limit per message


class SendError(RuntimeError):
    """The email could not be sent. The message never contains the API key."""


def recipients(settings: Settings) -> list[str]:
    return [a.strip() for a in settings.email_to.split(",") if a.strip()]


def check_email_settings(settings: Settings) -> None:
    """Fails early, before any AI money is spent, if the email setup is incomplete."""
    missing = [
        name for name, value in (
            ("RESEND_API_KEY", settings.resend_api_key), ("EMAIL_FROM", settings.email_from), ("EMAIL_TO", settings.email_to)
        ) if not value.strip()
    ]  # fmt: skip
    if missing:
        raise RuntimeError(f"Email is not set up: {', '.join(missing)} empty in .env (see README, 'Set up email').")
    if len(recipients(settings)) > MAX_RECIPIENTS:
        raise RuntimeError(f"EMAIL_TO has more than {MAX_RECIPIENTS} addresses, which Resend does not allow.")


def send_email(
    settings: Settings, subject: str, html: str, text: str, client: httpx.Client | None = None,
    attachments: list[dict[str, Any]] | None = None,
) -> str:  # fmt: skip
    """Sends one message to everyone in EMAIL_TO. Returns Resend's message id."""
    check_email_settings(settings)
    payload: dict[str, Any] = {"from": settings.email_from, "to": recipients(settings), "subject": subject, "html": html, "text": text}
    if attachments:
        payload["attachments"] = attachments
    headers = {"Authorization": f"Bearer {settings.resend_api_key}", "User-Agent": USER_AGENT}
    owns_client = client is None
    client = client or httpx.Client(timeout=30)
    try:
        response = client.post(RESEND_URL, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise SendError(f"Could not reach Resend: {type(exc).__name__}") from exc
    finally:
        if owns_client:
            client.close()
    if response.status_code >= 300:
        raise SendError(f"Resend refused the email (HTTP {response.status_code}): {response.text[:300]}")
    return str(response.json().get("id", ""))
