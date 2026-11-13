"""Template filters and small shared helpers."""
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from flask import current_app, url_for
from markupsafe import Markup, escape

FMT = "%Y-%m-%d %H:%M:%S"

# 24x24 stroke icons, drawn with currentColor.
ICONS = {
    "home": '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/><path d="M10 21v-6h4v6"/>',
    "tag": '<path d="M3 12V4a1 1 0 0 1 1-1h8l9 9-9 9Z"/><circle cx="7.5" cy="7.5" r="1.5"/>',
    "receipt": '<path d="M5 3h14v18l-3-2-2 2-2-2-2 2-2-2-3 2Z"/><path d="M9 8h6M9 12h6M9 16h3"/>',
    "folder": '<path d="M3 6.5A1.5 1.5 0 0 1 4.5 5H9l2 2.5h8.5A1.5 1.5 0 0 1 21 9v9.5a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18.5Z"/>',
    "sliders": '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "minus": '<path d="M5 12h14"/>',
    "bag": '<path d="M5 8h14l-1 12.5a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 6 20.5Z"/><path d="M9 10V6.5a3 3 0 0 1 6 0V10"/>',
    "arrow-right": '<path d="M5 12h14M13 6l6 6-6 6"/>',
    "arrow-down": '<path d="M12 4v16M6 14l6 6 6-6"/>',
    "chevron-left": '<path d="m15 18-6-6 6-6"/>',
    "chevron-right": '<path d="m9 18 6-6-6-6"/>',
    "chevron-up": '<path d="m6 15 6-6 6 6"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "x": '<path d="M18 6 6 18M6 6l12 12"/>',
    "check": '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    "alert": '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4M12 17h.01"/>',
    "lock": '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
    "card": '<rect x="2.5" y="5" width="19" height="14" rx="2"/><path d="M2.5 10h19M6.5 15h4"/>',
    "camera": '<path d="M4 8h3l2-3h6l2 3h3a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1Z"/><circle cx="12" cy="13.5" r="3.5"/>',
    "image": '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-5-5L5 21"/>',
    "trash": '<path d="M3 6h18M8 6V4h8v2M6 6l1 15h10l1-15"/>',
    "eye-off": '<path d="M3 3l18 18"/><path d="M10.6 5.1A10 10 0 0 1 12 5c5.5 0 9 5.5 9 7a11 11 0 0 1-2.6 3.6M6.3 6.3C4 7.8 3 10.6 3 12c0 1.5 3.5 7 9 7a9 9 0 0 0 4.4-1.1"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/>',
    "eye": '<path d="M2.5 12S6 5 12 5s9.5 7 9.5 7-3.5 7-9.5 7-9.5-7-9.5-7Z"/><circle cx="12" cy="12" r="3"/>',
    "copy": '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    "logout": '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5M21 12H9"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "box": '<path d="M21 8 12 3 3 8v8l9 5 9-5Z"/><path d="m3 8 9 5 9-5M12 13v8"/>',
    "phone": '<path d="M5 3h3.5l1.8 4.5-2.3 1.4a11 11 0 0 0 5.1 5.1l1.4-2.3L19 13.5V17a2 2 0 0 1-2 2A15 15 0 0 1 3 5a2 2 0 0 1 2-2Z"/>',
    "mail": '<rect x="2.5" y="5" width="19" height="14" rx="2"/><path d="m3 6.5 9 6.5 9-6.5"/>',
    "whatsapp": '<path d="M3.6 20.4 5 16.2A8.6 8.6 0 1 1 8.1 19Z"/><path d="M9.2 8.3c-.4 2.6 2.8 6.2 5.6 5.9l.9-1.4-1.9-1-.9.8c-.9-.4-2-1.5-2.4-2.4l.8-.9-1-1.9Z"/>',
    "pin": '<path d="M12 21s-7-6.2-7-11.5a7 7 0 0 1 14 0C19 14.8 12 21 12 21Z"/><circle cx="12" cy="9.5" r="2.5"/>',
    "star": '<path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9Z"/>',
    "store": '<path d="M4 9.5V20h16V9.5"/><path d="M3 4h18l1 5a3 3 0 0 1-5.5 1.5A3 3 0 0 1 12 12a3 3 0 0 1-4.5-1.5A3 3 0 0 1 2 9Z"/><path d="M10 20v-5h4v5"/>',
    "print": '<path d="M7 9V3h10v6"/><rect x="3" y="9" width="18" height="8" rx="2"/><path d="M7 14h10v7H7Z"/>',
    "truck": '<path d="M2.5 6h11v10h-11Z"/><path d="M13.5 9.5h4l3 3.5V16h-7"/><circle cx="6.5" cy="17.5" r="1.8"/><circle cx="17" cy="17.5" r="1.8"/>',
    "edit": '<path d="M4 20h4L19 9l-4-4L4 16Z"/><path d="m13.5 6.5 4 4"/>',
    "up": '<path d="M12 19V5M6 11l6-6 6 6"/>',
    "down": '<path d="M12 5v14M6 13l6 6 6-6"/>',
    "money": '<rect x="2.5" y="6" width="19" height="12" rx="2"/><circle cx="12" cy="12" r="2.8"/><path d="M6 9.5h.01M18 14.5h.01"/>',
    "sparkle": '<path d="M12 3c.6 4.6 2.4 6.4 7 7-4.6.6-6.4 2.4-7 7-.6-4.6-2.4-6.4-7-7 4.6-.6 6.4-2.4 7-7Z"/>',
    "list": '<path d="M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01"/>',
    "instagram": '<rect x="3" y="3" width="18" height="18" rx="5"/><circle cx="12" cy="12" r="4"/><path d="M17.5 6.5h.01"/>',
    "facebook": '<path d="M14 21v-8h3l.5-3.5H14V7.8c0-1 .3-1.8 1.8-1.8h1.8V3a24 24 0 0 0-2.7-.1C12.2 2.9 10.5 4.5 10.5 7.4v2.1h-3V13h3v8"/>',
}


def icon(name, cls=""):
    paths = ICONS.get(name, "")
    return Markup(
        f'<svg class="ic {cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
        f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{paths}</svg>'
    )


def services():
    return current_app.extensions["shop"]


def offset():
    return float(services().cfg["utc_offset_hours"])


def rands(cents):
    """R7.90, R145, R1 250 — whole rands without the .00."""
    cents = int(cents or 0)
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    if cents % 100 == 0:
        text = f"{cents // 100:,}"
    else:
        text = f"{cents / 100:,.2f}"
    return f"{sign}R{text.replace(',', ' ')}"


def money_input(cents):
    """Value for a price box: 7.90 / 145.00."""
    return "" if cents is None else f"{int(cents) / 100:.2f}"


def parse_money(value, allow_zero=True):
    """'R 22,50' -> 2250. None if it is not a sensible amount."""
    text = str(value or "").strip().replace("R", "").replace("r", "").replace(" ", "").replace(",", ".")
    if text == "":
        return 0 if allow_zero else None
    if text.count(".") > 1:
        return None
    try:
        cents = round(float(text) * 100)
    except ValueError:
        return None
    if cents < 0 or cents > 10_000_000 or (cents == 0 and not allow_zero):
        return None
    return cents


def parse_utc(value):
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:19], FMT)
    except ValueError:
        return None


def to_local(value):
    dt = parse_utc(value)
    return dt + timedelta(hours=offset()) if dt else None


def local_now():
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=offset())


def local_to_utc_str(dt):
    return (dt - timedelta(hours=offset())).strftime(FMT)


def localtime(value, fmt="%d %b %Y, %H:%M"):
    dt = to_local(value)
    return dt.strftime(fmt) if dt else ""


def ago(value):
    dt = parse_utc(value)
    if dt is None:
        return ""
    seconds = (datetime.now(timezone.utc).replace(tzinfo=None) - dt).total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        hours = int(seconds // 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = int(seconds // 86400)
    if days < 31:
        return f"{days} day{'s' if days != 1 else ''} ago"
    return localtime(value, "%d %b %Y")


def img(file):
    """URL of a saved photo, or None."""
    return url_for("store.media", name=file) if file else None


def public_base():
    """The shop's public address: the configured one, else the address this visitor used."""
    from flask import request
    return services().cfg["base_url"] or request.host_url.rstrip("/")


def paras(text):
    """Plain text with blank lines -> <p> paragraphs (escaped)."""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", str(text or "")) if b.strip()]
    return Markup("".join(f"<p>{escape(b).replace(chr(10), Markup('<br>'))}</p>" for b in blocks))


def bullets(text):
    """'Braai spice, joke, paper crown' -> ['Braai spice', 'Joke', 'Paper crown']."""
    parts = [p.strip() for p in re.split(r"[,\n;]+", str(text or "")) if p.strip()]
    return [p[:1].upper() + p[1:] for p in parts]


def initials(name):
    words = str(name or "?").split()
    return "".join(w[0] for w in words[:2]).upper() or "?"


def wa_link(number, text=""):
    digits = re.sub(r"\D", "", str(number or ""))
    if digits.startswith("0"):
        digits = "27" + digits[1:]
    if not digits:
        return None
    return f"https://wa.me/{digits}" + (f"?text={quote(text)}" if text else "")


def tel_link(number):
    digits = re.sub(r"[^\d+]", "", str(number or ""))
    return f"tel:{digits}" if digits else None


def register_filters(app):
    app.add_template_filter(rands)
    app.add_template_filter(money_input)
    app.add_template_filter(localtime)
    app.add_template_filter(ago)
    app.add_template_filter(paras)
    app.add_template_filter(bullets)
    app.add_template_filter(initials)
    app.add_template_global(icon)
    app.add_template_global(img)
    app.add_template_global(wa_link)
    app.add_template_global(tel_link)

    @app.context_processor
    def inject():
        svc = services()
        return {"build": svc.build, "app_version": svc.version, "year": local_now().year}
