"""Product photos, stored in the database (table `media`) under a name made from their contents.

Keeping them in the database means the shop works on servers without a permanent disk
(AWS Lambda) and everything is backed up in one place (Supabase).
"""
import hashlib
import re
import threading
from collections import OrderedDict

from .db import utcnow

MAX_BYTES = 4 * 1024 * 1024        # AWS Lambda can send back about 4.5 MB at most
NAME_RE = re.compile(r"^[0-9a-f]{20}\.(jpg|png|gif|webp)$")
TYPES = {".jpg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}

MAGIC = (
    (b"\xff\xd8\xff", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
)


class ImageError(Exception):
    pass


def sniff(data):
    for magic, ext in MAGIC:
        if data.startswith(magic):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def store_bytes(conn, data):
    if not data:
        raise ImageError("The photo was empty.")
    if len(data) > MAX_BYTES:
        raise ImageError("That photo is too big (max 4 MB). Try a smaller one.")
    ext = sniff(data)
    if ext is None:
        raise ImageError("That file is not a JPG, PNG, WebP or GIF image.")
    name = hashlib.sha256(data).hexdigest()[:20] + ext
    conn.execute(
        "INSERT INTO media(name, content_type, size, data, created_at) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(name) DO NOTHING",
        (name, TYPES[ext], len(data), data, utcnow()))
    return name


def store_upload(conn, file_storage):
    return store_bytes(conn, file_storage.read(MAX_BYTES + 1))


def is_stored(conn, name):
    """True if `name` is one of our saved photos (safe to reference from a form)."""
    return bool(name and NAME_RE.match(name)
                and conn.execute("SELECT 1 FROM media WHERE name = ?", (name,)).fetchone())


class _Cache:
    """Recently served photos kept in memory, so a busy page does not re-read them every time."""

    def __init__(self, limit_bytes=48 * 1024 * 1024):
        self.limit = limit_bytes
        self.size = 0
        self.items = OrderedDict()
        self.lock = threading.Lock()

    def get(self, name):
        with self.lock:
            item = self.items.get(name)
            if item is not None:
                self.items.move_to_end(name)
            return item

    def put(self, name, item):
        with self.lock:
            if name in self.items:
                return
            self.items[name] = item
            self.size += len(item[0])
            while self.size > self.limit and self.items:
                _, (data, _) = self.items.popitem(last=False)
                self.size -= len(data)


_cache = _Cache()


def load(conn, name):
    """(bytes, content type) for a saved photo, or None."""
    if not NAME_RE.match(name or ""):
        return None
    item = _cache.get(name)
    if item is None:
        row = conn.execute("SELECT content_type, data FROM media WHERE name = ?", (name,)).fetchone()
        if row is None:
            return None
        item = (bytes(row["data"]), row["content_type"])
        _cache.put(name, item)
    return item
