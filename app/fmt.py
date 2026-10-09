"""Turns numbers and codes into the short text shown in emails."""

from __future__ import annotations

SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "INR": "₹", "CAD": "C$", "AUD": "A$", "SGD": "S$", "JPY": "¥", "CNY": "CN¥"}
ROUND_LABELS = {
    "pre_seed": "Pre-seed", "seed": "Seed", "series_a": "Series A", "series_b": "Series B", "series_c": "Series C",
    "series_d": "Series D", "series_e_plus": "Series E+", "growth": "Growth round", "bridge": "Bridge round", "other": "",
}  # fmt: skip
REGION_LABELS = {"US": "the US", "Europe": "Europe", "Asia": "Asia", "RoW": "the rest of the world"}
EVENT_LABELS = {
    "shutdown": "Shut down", "down_round": "Down round", "flat_round": "Flat round", "acquihire": "Acqui-hire",
    "major_layoffs": "Layoffs", "distressed_sale": "Distressed sale",
}  # fmt: skip


def _scaled(value: float, prefix: str) -> str:
    for size, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if value >= size:
            number = f"{value / size:.1f}".removesuffix(".0")
            return f"{prefix}{number}{suffix}"
    return f"{prefix}{value:.0f}"


def fmt_usd(value: float) -> str:
    """1_200_000_000 -> '$1.2B'; 45_000_000 -> '$45M'."""
    return _scaled(value, "$")


def fmt_amount(amount: float | None, currency: str | None, amount_usd: float | None) -> str:
    """The amount as the source published it, plus an approximate USD figure for other currencies."""
    if amount is None or currency is None:
        return "Amount undisclosed"
    original = _scaled(amount, SYMBOLS.get(currency, f"{currency} "))
    if currency == "USD" or amount_usd is None:
        return original
    return f"{original} (~{fmt_usd(amount_usd)})"


def plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"
