# Rooi Nooi online shop

A small shop for Rooi Nooi (customised Christmas crackers, premade cracker boxes and
napkins) with an admin built like the vending machine one: phone first, bottom tabs on a
phone, left menu on a laptop.

Python + Flask. Locally it uses a SQLite file; live it uses **Supabase Postgres** and runs
on the **hosting provider's server** (gunicorn behind Nginx). Server setup: [HOSTING.md](HOSTING.md).

## How it is hosted

```
GitHub (timothywubbeling/RooiNooiEcommerceStore)
   │  push to main → GitHub runs the tests (Actions tab)
   ▼
Hosting provider's server: git pull + restart (HOSTING.md, "Updating")
   │  gunicorn behind Nginx, https://www.<domain>
   │  DATABASE_URL, YOCO_SECRET_KEY, PUDO_API_KEY in /etc/rooinooi.env
   ▼
Supabase Postgres (North EU, Stockholm): products, orders, settings and the photos
```

- **Admin:** the shop address + `/admin`. Starter password `1234`; you must pick your own
  at first login.
- The earlier **AWS Lambda copy** still runs on the same database until it is switched off.
  It is only updated when "Test and deploy" is run by hand in the Actions tab.

### Making a change

```bash
git add -A
git commit -m "What I changed"
git push
```

GitHub runs the tests. When they pass (green tick under **Actions**), ask the hosting
provider to update the server; it's one command for them. If a test fails (red cross),
fix it before asking them.

Products, prices, photos, options and settings are changed in the admin, not in code.

### Secrets

- The database connection string and the Yoco / PUDO keys live only on the server
  (`/etc/rooinooi.env`), never in this repository.
- After the shop moves to its domain, tap **Settings → Payments → Connect payment
  notifications** in the admin so Yoco sends confirmations to the new address.

## Run it on your own computer

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt pytest     # Windows
.venv\Scripts\python app.py                                         # http://localhost:5003
.venv\Scripts\python -m pytest -q                                   # run the tests
```

Without `DATABASE_URL` it uses `data/shop.db` (SQLite) and loads the starter products
from `seed/` the first time. Set `DATABASE_URL` to use a Postgres database instead.

## Where things live

```
rooi-nooi-shop/
  app.py               run locally
  lambda_handler.py    AWS Lambda entry point
  shop/                the app: routes, templates, styles, scripts
  seed/                starter catalogue + photos, loaded once into an empty database
  tests/               checks GitHub runs before every deploy
  deploy/              builds the Lambda package (build/rooi-nooi-shop-lambda.zip)
  .github/workflows/   the GitHub Actions deploy
```

## What the admin can do

- **Home**: new orders, unpaid total, today / this month, switch orders on or off, and a
  **Still to make** list (how many of each paper, filling, inside paper and name across
  open orders).
- **Orders**: filter New / Making / Ready / Unpaid / Done / Cancelled, search, open an
  order to WhatsApp or call the customer, move it along, **Mark as paid**, set the
  delivery cost once agreed, private notes, print, copy the customer's order-page link.
- **Products**: add, edit, copy, delete, reorder. Photos (first one is the main photo),
  price, short line, description, "what's inside", minimum per order,
  *In the shop / Sold out / Hidden*, "show on home page", and SEO text.
  - **Options customers choose**: "pick one" groups (outside paper, filling, inside
    paper, language) where every choice can add to the price, show its own photo and be
    marked sold out; and text options (a name on the cracker, +R1).
- **Categories**: name, description, order in the menu, hide/show, SEO text.
- **Settings**: shop name, top banner, contact details, home-page banner words and
  photos, About text, collection / delivery, the message shown after ordering, password.

## Payments (Yoco)

Card payments use **Rooi Nooi's own Yoco account** (not the vending machine's), the same
flow as the vending machine (`shop/payment.py`, `shop/orders.py`).

1. In Yoco: **Selling Online → Payment Gateway**, copy the **secret key** (`sk_live_…`,
   or `sk_test_…` to try it with test cards first).
2. In AWS Lambda `rooi-nooi-shop` → **Configuration → Environment variables**, add
   `YOCO_SECRET_KEY` with that key. Save.
3. In the shop admin → **Settings → Payments**, tap **Connect payment notifications**.
   The shop registers its webhook with Yoco and keeps the signing secret in the database.

How it works: checkout saves the order as **Awaiting card payment**, sends the customer
to Yoco, and Yoco's signed webhook (or a status check when the customer comes back) marks
it paid. Only then does it show up as a **New** order. Unfinished payments stay under
Orders → *Awaiting card payment*; the customer can pay later from their order page.

Without `YOCO_SECRET_KEY` the shop falls back to manual payment: orders arrive **Unpaid**
and you tap **Mark as paid**.

With card payments on, a shipping option whose fee is "to be confirmed" is hidden at
checkout, because the full amount is charged up front. Set a fixed fee to offer it.

## Shipping (PUDO)

Customers can choose **PUDO locker**: they pick their locker, pay the PUDO fee you set in
Settings, and you send it **locker to locker** (you drop the parcel in any PUDO locker).

1. In the PUDO / Courier Guy portal: **Settings → API Keys**, create a key.
2. In AWS Lambda → Environment variables, add `PUDO_API_KEY`.
3. In the admin → **Settings**, set the PUDO fee (**Check PUDO's prices** shows the
   current rates) and your usual parcel size.

Then customers search real PUDO lockers at checkout, and on each paid order you tap
**Book PUDO shipment**: the shop gets the waybill number and the **drop-off PIN**, and
**Waybill label (PDF)** opens the label to print. The customer sees the tracking number
on their order page and gets PUDO's SMS when it arrives.

Without `PUDO_API_KEY` customers type their locker by name and you book in the portal.

## Local testing

`SHOP_FAKE_PAYMENTS=1` and `SHOP_FAKE_PUDO=1` switch on a pretend Yoco page and a pretend
PUDO with three lockers, for trying the whole flow without real money or shipments. The
tests use them too.

## Limits to know about

- **AWS Free plan:** the account can only use the Stockholm region, and the free plan ends
  on 13 May 2027 (or when the $100 credit runs out). Upgrade the AWS account to the
  paid plan before then or the shop goes offline. At this traffic Lambda stays inside the
  always-free allowance.
- **Supabase free plan:** a project with no activity for a week is paused. Visitors to the
  shop count as activity; if it does pause, press *Restore* in the Supabase dashboard.
- Photos up to 4 MB each (the admin shrinks phone photos automatically).
