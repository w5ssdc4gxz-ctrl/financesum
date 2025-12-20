#!/usr/bin/env python3
"""Populate Stripe env vars in the repo-root `.env` from your clipboard (macOS).

Usage:
  1) Copy lines like:
       STRIPE_SECRET_KEY=sk_test_...
       STRIPE_PUBLISHABLE_KEY=pk_test_...
     (optionally also STRIPE_WEBHOOK_SECRET, STRIPE_PRICE_ID / STRIPE_PRICE_LOOKUP_KEY, SITE_URL)
  2) Run:
       python3 scripts/set_stripe_env_from_clipboard.py

This script never prints secret values; it only reports which keys were written.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


WANTED_KEYS = {
    "STRIPE_SECRET_KEY",
    "STRIPE_PUBLISHABLE_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "STRIPE_PRICE_ID",
    "STRIPE_PRICE_LOOKUP_KEY",
    "SITE_URL",
}


def _read_clipboard() -> str:
    try:
        return subprocess.check_output(["pbpaste"], text=True)
    except FileNotFoundError as exc:
        raise RuntimeError("pbpaste not found (this helper currently supports macOS clipboard only).") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("Unable to read clipboard via pbpaste.") from exc


def _parse_env_lines(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key in WANTED_KEYS and value:
            found[key] = value
    return found


def _upsert_env_file(env_path: Path, updates: dict[str, str]) -> None:
    existing_lines: list[str] = []
    if env_path.exists():
        existing_lines = env_path.read_text().splitlines()

    preserved: list[str] = []
    for raw_line in existing_lines:
        line = raw_line.rstrip("\n")
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            preserved.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in updates:
            continue
        preserved.append(line)

    if preserved and preserved[-1].strip():
        preserved.append("")

    for key in sorted(updates.keys()):
        preserved.append(f"{key}={updates[key]}")

    env_path.write_text("\n".join(preserved).rstrip("\n") + "\n")


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    env_path = repo_root / ".env"

    clipboard = _read_clipboard()
    updates = _parse_env_lines(clipboard)
    if not updates:
        raise RuntimeError(
            "Clipboard did not contain any STRIPE_* lines. Copy your STRIPE_SECRET_KEY=... and STRIPE_PUBLISHABLE_KEY=... lines, then run again."
        )

    required = {"STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY"}
    missing = sorted(required - set(updates.keys()))
    if missing:
        raise RuntimeError(f"Clipboard is missing required keys: {', '.join(missing)}")

    _upsert_env_file(env_path, updates)
    written = ", ".join(sorted(updates.keys()))
    print(f"Updated {env_path} with: {written}")


if __name__ == "__main__":
    main()

