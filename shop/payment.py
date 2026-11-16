"""How orders get paid.

With YOCO_SECRET_KEY set (AWS Lambda > Configuration > Environment variables) customers pay
by card on Yoco's checkout page, the same flow as the vending machine:

  1. store.api_order() saves the order as "pending" and asks Yoco for a checkout
  2. the customer pays on Yoco's page and comes back to the order page
  3. Yoco's webhook (signature-checked) or a status check marks the order paid,
     and only then does it show up in the admin as a New order

Without a key the shop falls back to manual payment: orders arrive unpaid and Rooi Nooi
arranges payment with the customer.

The Yoco secret key is only ever read from the environment, never stored in code or the
database. The webhook signing secret is saved in the database when Rooi Nooi taps
"Connect payment notifications" in Settings.
"""
import base64
import hashlib
import hmac
import json
import threading
import time
import urllib.error
import urllib.request
import uuid

YOCO_API = "https://payments.yoco.com/api"

PAID = "paid"
FAILED = "failed"
PENDING = "pending"


class PaymentError(Exception):
    pass


def normalise_status(raw):
    """Map Yoco's checkout status onto paid / failed / pending."""
    status = (raw or "").strip().lower()
    if status in ("completed", "complete", "succeeded", "successful", "paid"):
        return PAID
    if status in ("expired", "cancelled", "canceled", "failed", "declined"):
        return FAILED
    return PENDING


class ManualPayments:
    """No online payment: the order page tells the customer what happens next."""
    name = "manual"
    label = "manual (no Yoco key set)"
    online = False
    is_fake = False
    is_test = False

    def create_checkout(self, *a, **kw):
        raise PaymentError("Card payments are not set up.")

    def checkout_status(self, checkout_id):
        return None


class YocoPayments:
    name = "yoco"
    online = True
    is_fake = False

    def __init__(self, secret_key, timeout=15):
        self.secret_key = secret_key
        self.timeout = timeout

    @property
    def is_test(self):
        return self.secret_key.startswith("sk_test_")

    @property
    def label(self):
        return "Yoco (TEST key)" if self.is_test else "Yoco (live)"

    def _call(self, method, path, body=None, idempotency_key=None):
        headers = {"Authorization": f"Bearer {self.secret_key}", "Content-Type": "application/json",
                   "Accept": "application/json"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(YOCO_API + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                payload = json.loads(raw) if raw else {}
            except ValueError:
                payload = {"raw": raw[:300].decode("utf-8", "replace")}
            return exc.code, payload
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise PaymentError("Could not reach Yoco. Please try again in a moment.") from exc

    def create_checkout(self, order, success_url, cancel_url, failure_url):
        status, data = self._call("POST", "/checkouts", {
            "amount": int(order["total_cents"]),
            "currency": "ZAR",
            "successUrl": success_url,
            "cancelUrl": cancel_url,
            "failureUrl": failure_url,
            "externalId": order["id"],
            "metadata": {"orderId": order["id"], "customer": order["customer_name"][:80]},
        }, idempotency_key=f"{order['id']}-{uuid.uuid4().hex[:8]}")
        if status >= 400 or not data.get("redirectUrl") or not data.get("id"):
            print(f"!! Yoco checkout failed for {order['id']}: HTTP {status} {str(data)[:300]}")
            raise PaymentError("The card payment service refused the request.")
        return {"id": data["id"], "redirect_url": data["redirectUrl"]}

    def checkout_status(self, checkout_id):
        """paid / failed / pending, or None if Yoco could not be asked."""
        try:
            status, data = self._call("GET", f"/checkouts/{checkout_id}")
        except PaymentError as exc:
            print(f"!! Yoco status check failed for {checkout_id}: {exc}")
            return None
        if status != 200:
            print(f"!! Yoco status check for {checkout_id}: HTTP {status}")
            return None
        return normalise_status(data.get("status"))

    def register_webhook(self, url, name="Rooi Nooi shop"):
        """(Re)register the payment webhook. Returns (webhook id, signing secret)."""
        # Yoco allows one webhook per key, and its secret is only shown when it is made. So any
        # earlier address of this shop (an old domain, the AWS copy) is removed first; webhooks
        # that belong to something else on the Yoco account are left alone.
        status, data = self._call("GET", "/webhooks")
        if status == 200:
            hooks = data.get("subscriptions", data) if isinstance(data, dict) else data
            for hook in hooks or []:
                if (isinstance(hook, dict) and hook.get("id")
                        and str(hook.get("url") or "").rstrip("/").endswith("/webhook/yoco")):
                    d_status, d_data = self._call("DELETE", f"/webhooks/{hook['id']}")
                    print(f">> removed old Yoco webhook {hook.get('url')}: HTTP {d_status}")
        else:
            print(f"!! listing Yoco webhooks: HTTP {status} {str(data)[:300]}")
        status, data = self._call("POST", "/webhooks", {"name": name, "url": url})
        if status >= 400 or not isinstance(data, dict) or not data.get("secret"):
            detail = data if not isinstance(data, dict) else (
                data.get("description") or data.get("message") or data.get("errorMessage") or data)
            print(f"!! Yoco webhook registration failed: HTTP {status} {str(data)[:300]}")
            raise PaymentError(f"Yoco refused the webhook (HTTP {status}): {str(detail)[:200]}")
        return data.get("id", ""), data["secret"]


class FakePayments:
    """Pretend Yoco for tests and local previews (SHOP_FAKE_PAYMENTS=1). Never used live."""
    name = "yoco"
    online = True
    is_fake = True
    is_test = True
    label = "pretend card payments (dev only)"

    def __init__(self):
        self._lock = threading.Lock()
        self.checkouts = {}

    def create_checkout(self, order, success_url, cancel_url, failure_url):
        checkout_id = "fake_" + uuid.uuid4().hex[:12]
        with self._lock:
            self.checkouts[checkout_id] = {"order_id": order["id"], "amount": order["total_cents"],
                                           "status": PENDING, "success_url": success_url, "cancel_url": cancel_url}
        return {"id": checkout_id, "redirect_url": f"/dev/fake-pay/{checkout_id}"}

    def checkout_status(self, checkout_id):
        with self._lock:
            entry = self.checkouts.get(checkout_id)
            return entry["status"] if entry else None

    def set_status(self, checkout_id, status):
        with self._lock:
            if checkout_id in self.checkouts:
                self.checkouts[checkout_id]["status"] = status
            return self.checkouts.get(checkout_id)

    def register_webhook(self, url, name="Rooi Nooi shop"):
        return "fake_hook", "whsec_" + base64.b64encode(b"fake-webhook-secret-123456").decode()


def build_payments(cfg):
    if cfg.get("fake_payments"):
        return FakePayments()
    key = (cfg.get("yoco_secret_key") or "").strip()
    if key.startswith(("sk_live_", "sk_test_")):
        return YocoPayments(key)
    return ManualPayments()


def verify_webhook_signature(secret, headers, body, tolerance=300, now=None):
    """Check a Yoco (Standard Webhooks) signature. `body` is the raw request bytes."""
    msg_id = headers.get("webhook-id")
    timestamp = headers.get("webhook-timestamp")
    signatures = headers.get("webhook-signature")
    if not (secret and msg_id and timestamp and signatures):
        return False
    try:
        if abs((now or time.time()) - int(timestamp)) > tolerance:
            return False
        key = base64.b64decode(secret.split("_", 1)[1] if secret.startswith("whsec_") else secret)
    except (ValueError, TypeError):
        return False
    signed = f"{msg_id}.{timestamp}.".encode() + body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    for part in signatures.split():
        _, _, sig = part.partition(",")
        if sig and hmac.compare_digest(sig, expected):
            return True
    return False


def sign_for_tests(secret, msg_id, timestamp, body):
    key = base64.b64decode(secret.split("_", 1)[1] if secret.startswith("whsec_") else secret)
    sig = base64.b64encode(hmac.new(key, f"{msg_id}.{timestamp}.".encode() + body, hashlib.sha256).digest())
    return "v1," + sig.decode()
