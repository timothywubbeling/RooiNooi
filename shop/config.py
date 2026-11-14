"""Runtime configuration.

Locally the shop uses a SQLite file in data/. In the cloud it uses Supabase Postgres:
set DATABASE_URL to the Supabase "Transaction pooler" connection string.

Optional data/config.json can hold the same keys for a local run. Environment
variables win over the file. Nothing is ever written here: the session secret is
kept in the database, so the app also runs on read-only servers such as AWS Lambda.
"""
import json
import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
SEED_DIR = APP_DIR / "seed"


DEFAULTS = {
    "port": 5003,
    "base_url": "",               # empty = use the address the visitor came in on
    "database_url": "",           # empty = local SQLite file data/shop.db
    "flask_secret": "",           # empty = generated once and kept in the database
    "utc_offset_hours": 2,        # South Africa: UTC+2, no daylight saving
    # Rooi Nooi's own Yoco account (not the vending machine's). Set YOCO_SECRET_KEY in the
    # Lambda's environment variables; without it orders are paid manually.
    "yoco_secret_key": "",
    "yoco_webhook_secret": "",
    "fake_payments": False,       # dev/tests only: a pretend Yoco page on /dev/fake-pay
    # PUDO (The Courier Guy lockers) API key from the PUDO portal: Settings > API Keys.
    "pudo_api_key": "",
    "fake_pudo": False,           # dev/tests only: a pretend PUDO with three lockers
}

ENV = {
    "port": ("SHOP_PORT", int),
    "base_url": ("SHOP_BASE_URL", str),
    "database_url": ("DATABASE_URL", str),
    "flask_secret": ("FLASK_SECRET", str),
    "utc_offset_hours": ("SHOP_UTC_OFFSET", float),
    "yoco_secret_key": ("YOCO_SECRET_KEY", str),
    "yoco_webhook_secret": ("YOCO_WEBHOOK_SECRET", str),
    "fake_payments": ("SHOP_FAKE_PAYMENTS", lambda v: str(v).strip().lower() in ("1", "true", "yes", "on")),
    "pudo_api_key": ("PUDO_API_KEY", str),
    "fake_pudo": ("SHOP_FAKE_PUDO", lambda v: str(v).strip().lower() in ("1", "true", "yes", "on")),
}


def data_dir():
    return Path(os.environ.get("SHOP_DATA_DIR") or APP_DIR / "data")


def load_config(directory=None):
    directory = Path(directory or data_dir())
    stored = {}
    path = directory / "config.json"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            stored = json.load(f)

    cfg = dict(DEFAULTS)
    cfg.update(stored)
    for key, (name, cast) in ENV.items():
        if os.environ.get(name) not in (None, ""):
            cfg[key] = cast(os.environ[name])

    cfg["base_url"] = str(cfg["base_url"] or "").rstrip("/")
    cfg["database_url"] = str(cfg["database_url"] or "").strip()
    cfg["data_dir"] = directory
    cfg["db_path"] = directory / "shop.db"
    return cfg
