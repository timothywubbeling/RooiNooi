# Hosting the Rooi Nooi shop on your own server (Nginx reverse proxy)

The shop is a **Python 3 Flask app** (WSGI). There is no build step and no deployment
tool beyond this file: clone, install, run it with gunicorn, put Nginx in front.

- Entry point: `wsgi:app` (gunicorn) — also `passenger_wsgi.py` if you prefer Passenger
- Database: **external Supabase Postgres** (via `DATABASE_URL`). Nothing to install,
  no MySQL needed. Product photos are stored in the database too.
- The app writes **no files** to disk; it can run from a read-only checkout.
- On first start it creates its tables if missing (they already exist in Supabase).

## Requirements

- Linux, Python **3.11+** (3.13 tested), `python3-venv`, git
- Outbound internet from the server: Supabase pooler `aws-0-eu-north-1.pooler.supabase.com`
  port **6543**, `payments.yoco.com` 443, `api-pudo.co.za` 443
- ~200 MB RAM is plenty

## 1. Get the code

```bash
sudo useradd --system --create-home --home-dir /opt/rooinooi rooinooi
sudo -u rooinooi git clone https://github.com/timothywubbeling/RooiNooiEcommerceStore.git /opt/rooinooi/app
cd /opt/rooinooi/app
sudo -u rooinooi python3 -m venv .venv
sudo -u rooinooi .venv/bin/pip install -r requirements.txt gunicorn
```

The repo is private: use a GitHub **deploy key** (read-only) or the access the owner gives you.

## 2. Settings (environment variables)

Create `/etc/rooinooi.env` (owner root, `chmod 600`). The owner will send the secret
values privately — never commit them.

```
DATABASE_URL=postgresql://postgres.<ref>:<password>@aws-0-eu-north-1.pooler.supabase.com:6543/postgres
YOCO_SECRET_KEY=sk_live_...
PUDO_API_KEY=...            # optional, enables the PUDO locker list + booking
SHOP_BASE_URL=https://www.<domain>
```

| Variable | Required | What it is |
| --- | --- | --- |
| `DATABASE_URL` | yes | Supabase *Transaction pooler* connection string |
| `YOCO_SECRET_KEY` | for card payments | Yoco secret key (live) |
| `PUDO_API_KEY` | optional | PUDO / Courier Guy API key |
| `SHOP_BASE_URL` | recommended | Public address, used in links Yoco sends customers back to |

## 3. Run it with systemd

`/etc/systemd/system/rooinooi.service`:

```ini
[Unit]
Description=Rooi Nooi shop
After=network-online.target

[Service]
User=rooinooi
WorkingDirectory=/opt/rooinooi/app
EnvironmentFile=/etc/rooinooi.env
ExecStart=/opt/rooinooi/app/.venv/bin/gunicorn --workers 2 --threads 4 --timeout 60 --bind 127.0.0.1:8000 wsgi:app
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now rooinooi
curl -s http://127.0.0.1:8000/healthz      # {"ok": true}
journalctl -u rooinooi -f                  # logs
```

## 4. Nginx

```nginx
server {
    listen 443 ssl http2;
    server_name www.<domain> <domain>;
    # ssl_certificate / ssl_certificate_key: your existing certificate

    client_max_body_size 20m;          # admin photo uploads

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
        proxy_read_timeout 60s;
    }
}
server {
    listen 80;
    server_name www.<domain> <domain>;
    return 301 https://www.<domain>$request_uri;
}
```

The app trusts exactly **one** proxy hop (`X-Forwarded-*`), so keep Nginx as the only
proxy in front, or tell the owner if there is another layer (Cloudflare etc.).

## 5. Check

- `https://www.<domain>/` shows the shop, `/healthz` returns `{"ok": true}`
- `https://www.<domain>/admin` shows the admin login

The owner then reconnects Yoco payment notifications in the admin (Settings → Payments),
so Yoco sends payment confirmations to `https://www.<domain>/webhook/yoco`.

## Updating

```bash
cd /opt/rooinooi/app && sudo -u rooinooi git pull && sudo -u rooinooi .venv/bin/pip install -r requirements.txt && sudo systemctl restart rooinooi
```

Tests (optional): `.venv/bin/pip install pytest && .venv/bin/python -m pytest -q`
(they use a throwaway SQLite database, never the live one).

Note: `.github/workflows/deploy.yml` deploys to the owner's AWS Lambda copy and can be ignored here.
