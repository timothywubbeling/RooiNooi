# Rooi Nooi online shop

A small shop for Rooi Nooi (customised Christmas crackers, premade cracker boxes and
napkins) with an admin built like the vending machine one: phone first, bottom tabs on a
phone, left menu on a laptop.

Python + Flask. Locally it uses a SQLite file; live it uses **Supabase Postgres** and runs
on **AWS Lambda**.

## How it is hosted

```
GitHub (timothywubbeling/RooiNooi)
   │  push to main
   ▼
GitHub Actions ── runs the tests ── builds the package ── updates the Lambda
                                                            │
AWS Lambda "rooi-nooi-shop" (eu-north-1, Stockholm)  ◄──────┘
   │  public address (Function URL)
   │  DATABASE_URL environment variable
   ▼
Supabase Postgres (North EU, Stockholm): products, orders, settings and the photos
```

- **Live shop:** https://kcrafybzwmlg2dvsfgq3asprwm0pekem.lambda-url.eu-north-1.on.aws/
- **Admin:** add `/admin` to that address. Starter password `1234`, you must pick your own
  at first login.

### Making a change

Edit the code, then:

```bash
git add -A
git commit -m "What I changed"
git push
```

GitHub runs the tests (`tests/`) and, if they pass, puts the new version live within a
couple of minutes. Watch it under the repository's **Actions** tab. If a test fails,
nothing is deployed and the live shop keeps running the previous version.

Products, prices, photos, options and settings are changed in the admin, not in code.

### Secrets

- The Supabase connection string is only stored in the Lambda's environment variables
  (`DATABASE_URL`), never in this repository.
- GitHub deploys with an access key stored as repository secrets (`AWS_ACCESS_KEY_ID`,
  `AWS_SECRET_ACCESS_KEY`) for the IAM user `rooi-nooi-shop-github-deploy`, which may only
  update this one function's code. (The keyless OpenID Connect link needs the paid AWS plan.)
- The admin password and session key live in the database.

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

## Payments

Not connected yet. Orders arrive as **Unpaid**; Rooi Nooi arranges payment and taps
**Mark as paid**. Yoco will be added on **Rooi Nooi's own Yoco account** using the same
flow as the vending machine; `shop/payment.py` explains where each piece goes. The Yoco
secret key will go in the Lambda's environment variables (`YOCO_SECRET_KEY`).

## Limits to know about

- **AWS Free plan:** the account can only use the Stockholm region, and the free plan ends
  on 13 May 2027 (or when the $100 credit runs out). Upgrade the AWS account to the
  paid plan before then or the shop goes offline. At this traffic Lambda stays inside the
  always-free allowance.
- **Supabase free plan:** a project with no activity for a week is paused. Visitors to the
  shop count as activity; if it does pause, press *Restore* in the Supabase dashboard.
- Photos up to 4 MB each (the admin shrinks phone photos automatically).
