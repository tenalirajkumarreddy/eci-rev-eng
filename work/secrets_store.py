#!/usr/bin/env python3
"""secrets_store.py - keep live sessions out of the committed config.

`work/live_config.json` is tracked by git, so anything written there ends up in
history.  A successful OTP login hands back a real 8-hour Keycloak session
(access_token + refresh_token + atkn_bnd + rtkn_bnd), and `git add -A` would
publish it.

So live credentials live in `work/secrets.json` (git-ignored) and the tracked
config keeps only non-secret knobs.  Callers use `read_config()`, which returns
the public config overlaid with the secrets, so existing code is unchanged.

    cfg = secrets_store.read_config()          # {"api_key": ..., "bearer": ...}
    secrets_store.write_secrets({"bearer": "Bearer ..."})
"""

from __future__ import annotations

import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
PUBLIC = BASE / "live_config.json"
SECRET = BASE / "secrets.json"

# Everything a login writes that must never be committed.
SECRET_KEYS = ("bearer", "access_token", "refresh_token", "atkn_bnd", "rtkn_bnd")


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def read_secrets() -> dict:
    return _load(SECRET)


def read_config() -> dict:
    """Public knobs + live secrets (secrets win)."""
    return {**_load(PUBLIC), **read_secrets()}


def write_secrets(update: dict) -> list[str]:
    secrets = read_secrets()
    written = []
    for key in SECRET_KEYS:
        if update.get(key):
            secrets[key] = update[key]
            written.append(key)
    if update.get("last_login_mobile"):
        secrets["last_login_mobile"] = update["last_login_mobile"]
    for key in ("device_id",):  # not a credential, but session-scoped
        if update.get(key):
            secrets[key] = update[key]
    SECRET.parent.mkdir(parents=True, exist_ok=True)
    SECRET.write_text(json.dumps(secrets, indent=2) + "\n", encoding="utf-8")
    return written


def write_public(update: dict) -> None:
    """Rewrite the tracked config, refusing to write secrets into it."""
    for key in update:
        if key in SECRET_KEYS:
            raise ValueError(f"refusing to write secret {key!r} into {PUBLIC.name}")
    PUBLIC.write_text(json.dumps(dict(update), indent=2) + "\n", encoding="utf-8")


def strip_public_secrets() -> list[str]:
    """One-off scrub: blank any secrets already sitting in the tracked file."""
    public = _load(PUBLIC)
    scrubbed = [k for k in SECRET_KEYS if public.get(k)]
    for key in scrubbed:
        public[key] = ""
    if scrubbed:
        PUBLIC.write_text(json.dumps(public, indent=2) + "\n", encoding="utf-8")
    return scrubbed
