"""PUDO (The Courier Guy lockers) shipping.

With PUDO_API_KEY set (AWS Lambda > Configuration > Environment variables; the key is made in
the PUDO portal under Settings > API Keys) the shop:

  - lets customers pick a real PUDO locker at checkout (list cached for a few hours)
  - books a Locker-to-Locker shipment from the admin order page with one tap
  - shows the waybill number and the drop-off PIN, and opens the waybill label PDF

Rooi Nooi then drops the parcel in any PUDO locker using the PIN; the customer gets an SMS
from PUDO when it reaches their locker. Without a key, customers type their locker by name
and shipments are booked in the PUDO portal by hand.
"""
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

PUDO_ROOT = "https://api-pudo.co.za"
PUDO_API = PUDO_ROOT + "/api/v1"
SIZES = {
    "XS": "Extra small (up to 2 kg, 60×17×8 cm)",
    "S": "Small (up to 5 kg, 60×41×8 cm)",
    "M": "Medium (up to 10 kg, 60×41×19 cm)",
    "L": "Large (up to 15 kg, 60×41×41 cm)",
    "XL": "Extra large (up to 20 kg, 60×41×69 cm)",
}
LOCKER_CACHE_SECONDS = 6 * 3600


class PudoError(Exception):
    pass


def mobile(number):
    """'+27 82 123 4567' -> '0821234567' (the format PUDO's SMS service expects)."""
    digits = re.sub(r"\D", "", str(number or ""))
    if digits.startswith("27") and len(digits) == 11:
        digits = "0" + digits[2:]
    return digits


def _clean_locker(raw):
    place = raw.get("place") if isinstance(raw.get("place"), dict) else {}
    kind = raw.get("type") if isinstance(raw.get("type"), dict) else {}
    return {
        "code": str(raw.get("code") or "").strip(),
        "name": " ".join(str(raw.get("name") or "").split()),
        "address": " ".join(str(raw.get("address") or "").split()),
        "town": " ".join(str(place.get("town") or "").split()),
        "kind": kind.get("name") or "Locker",
    }


class PudoClient:
    is_fake = False

    def __init__(self, api_key, timeout=20):
        self.api_key = api_key
        self.timeout = timeout
        self._lockers = None
        self._lockers_at = 0
        self._lock = threading.Lock()

    def _call(self, method, url, body=None, auth=True):
        headers = {"Accept": "application/json", "Content-Type": "application/json",
                   "User-Agent": "RooiNooiShop/1.0 (+https://rooinooi.co.za)"}   # not urllib's default
        if auth:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                ctype = resp.headers.get("Content-Type", "")
                if "json" in ctype or raw[:1] in (b"{", b"["):
                    return json.loads(raw or b"null")
                return raw.decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            raw = exc.read()[:600].decode("utf-8", "replace")
            print(f"!! PUDO {method} {url.split('?')[0]} -> HTTP {exc.code}: {raw[:300]}")
            if exc.code in (401, 403):
                raise PudoError("PUDO did not accept the API key. Check PUDO_API_KEY in the Lambda settings.") from exc
            try:
                detail = json.loads(raw)
                message = detail.get("message") or detail.get("error") or raw
            except ValueError:
                message = raw
            raise PudoError(f"PUDO said: {str(message)[:200]}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise PudoError("Could not reach PUDO. Try again in a moment.") from exc

    # ── lockers ─────────────────────────────────────────────────────────
    def lockers(self):
        """Every PUDO locker and kiosk, cached for a few hours."""
        with self._lock:
            if self._lockers is not None and time.time() - self._lockers_at < LOCKER_CACHE_SECONDS:
                return self._lockers
        data = self._call("GET", PUDO_API + "/lockers-data")
        if isinstance(data, dict):
            data = data.get("data") or data.get("lockers") or []
        lockers = [l for l in (_clean_locker(x) for x in data or [] if isinstance(x, dict)) if l["code"]]
        lockers.sort(key=lambda l: (l["town"].lower(), l["name"].lower()))
        with self._lock:
            self._lockers, self._lockers_at = lockers, time.time()
        return lockers

    def locker(self, code):
        code = str(code or "").strip().upper()
        return next((l for l in self.lockers() if l["code"].upper() == code), None)

    # ── rates ───────────────────────────────────────────────────────────
    def rates(self, terminal_id):
        """Locker-to-Locker prices (incl. VAT) per size for sending to `terminal_id`."""
        data = self._call("POST", PUDO_API + "/rates", {
            "collection_address": {"type": "locker"},
            "delivery_address": {"terminal_id": terminal_id},
        })
        out = []
        for r in (data or {}).get("rates", []) if isinstance(data, dict) else []:
            level = r.get("service_level") or {}
            out.append({"code": level.get("code"), "name": level.get("name"), "rate": r.get("rate")})
        return out

    # ── shipments ───────────────────────────────────────────────────────
    def create_shipment(self, order, terminal_id, size, sender):
        """Book a Locker-to-Locker shipment. Returns id, waybill, pin, status, rate."""
        if size not in SIZES:
            raise PudoError("Choose a parcel size.")
        customer_email = order["email"] or sender.get("email") or ""
        data = self._call("POST", PUDO_API + "/shipments", {
            "collection_address": {"type": "locker"},
            "special_instructions_collection": f"Rooi Nooi order {order['id']}",
            "collection_contact": {"name": sender.get("name") or "Rooi Nooi",
                                   "email": sender.get("email") or "", "mobile_number": mobile(sender.get("phone"))},
            "delivery_address": {"terminal_id": terminal_id},
            "delivery_contact": {"name": order["customer_name"], "email": customer_email,
                                 "mobile_number": mobile(order["phone"])},
            "service_level_code": f"L2L{size} - ECO",
            "customer_reference": order["id"],
        })
        if not isinstance(data, dict) or not data.get("id"):
            raise PudoError("PUDO did not return a shipment. Check the PUDO portal before trying again.")
        return {"id": str(data["id"]), "waybill": data.get("custom_tracking_reference") or "",
                "pin": str(data.get("pincode") or ""), "status": data.get("status") or "",
                "rate": data.get("rate")}

    def label_url(self, shipment_id):
        """A short-lived link to the waybill PDF."""
        query = urllib.parse.urlencode({"api_key": self.api_key})
        data = self._call("GET", f"{PUDO_ROOT}/generate/waybill/{shipment_id}?{query}", auth=False)
        if isinstance(data, str) and data.strip().startswith("http"):
            return data.strip().strip('"')
        if isinstance(data, dict):
            for value in data.values():
                if isinstance(value, str) and value.startswith("http"):
                    return value
        raise PudoError("PUDO did not return a label link.")


class FakePudo(PudoClient):
    """Pretend PUDO for tests and local previews (SHOP_FAKE_PUDO=1)."""
    is_fake = True

    def __init__(self):
        super().__init__("fake")
        self.shipments = []

    def lockers(self):
        return [
            {"code": "CG54", "name": "Sasol Rivonia Uplifted", "address": "375 Rivonia Rd, Rivonia, Sandton",
             "town": "Sandton", "kind": "Locker"},
            {"code": "CG107", "name": "Engen Garsfontein", "address": "Garsfontein Rd, Garsfontein, Pretoria",
             "town": "Pretoria", "kind": "Locker"},
            {"code": "CG10", "name": "Menlyn Maine", "address": "Corner Aramist & Corobay, Menlyn, Pretoria",
             "town": "Pretoria", "kind": "Locker"},
        ]

    def rates(self, terminal_id):
        return [{"code": f"L2L{s} - ECO", "name": f"Locker to Locker {s}", "rate": f"{50 + 10 * i}.00"}
                for i, s in enumerate(SIZES)]

    def create_shipment(self, order, terminal_id, size, sender):
        self.shipments.append((order["id"], terminal_id, size))
        n = len(self.shipments)
        return {"id": str(900 + n), "waybill": f"PUDOD{n:06d}", "pin": "270089", "status": "deposit-pending",
                "rate": 60}

    def label_url(self, shipment_id):
        return f"https://example.com/fake-waybill-{shipment_id}.pdf"


def build_pudo(cfg):
    if cfg.get("fake_pudo"):
        return FakePudo()
    key = (cfg.get("pudo_api_key") or "").strip()
    return PudoClient(key) if key else None
