#!/usr/bin/env python3
"""Entry point: `python app.py` starts the shop and the admin on one port."""
import sys

from shop import VERSION, create_app
from shop.config import load_config


def main():
    sys.stdout.reconfigure(line_buffering=True)
    cfg = load_config()
    app = create_app(cfg)
    svc = app.extensions["shop"]
    print(f">> Rooi Nooi shop {VERSION} on port {cfg['port']}")
    print(f"   database: {'Postgres (DATABASE_URL)' if svc.db.kind == 'pg' else cfg['db_path']}")
    print(f"   payments: {svc.payments.label}")
    print(f"   shop:  http://localhost:{cfg['port']}/")
    print(f"   admin: http://localhost:{cfg['port']}/admin")
    app.run(host="0.0.0.0", port=cfg["port"], debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
