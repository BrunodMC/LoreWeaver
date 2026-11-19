"""Who is the DM: one password, a signed session cookie, and deny-by-default for everyone else.

Anyone without a DM session is a player: forced into party view, and only allowed the read-only,
party-safe pages listed in `party_safe`. Every other route (every edit, every POST, every DM page)
answers only to the DM. A new route is therefore DM-only until someone adds it to the list on purpose.

Before a password is set, the machine running the server is trusted (a request from loopback with no
proxy in between), so the app still works out of the box. Anyone else gets party view. Once a password
exists, everyone signs in, the host machine included: a tunnel (Cloudflare, Tailscale) also arrives
from loopback, so loopback alone can't vouch for anybody.

    python -m src.app.auth set-password      (asks twice; signs every existing session out)
    python -m src.app.auth clear-password    (back to "trust this machine only")
"""
import getpass
import hashlib
import hmac
import secrets
import sqlite3
import sys
import time
from contextlib import closing

from starlette.requests import HTTPConnection

from . import db
from .entity_types import ENTITY_TYPES

COOKIE = "dm_session"
SESSION_DAYS = 30
HASH_KEY = "dm_password_hash"
SECRET_KEY = "session_secret"
ITERATIONS = 600_000  # PBKDF2-SHA256, OWASP's 2023 figure; ~0.3 s per check, fine for one login form
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
# Headers a reverse proxy or tunnel adds. With any of them, a loopback address is the proxy, not a person.
PROXY_HEADERS = ("x-forwarded-for", "forwarded", "x-real-ip", "cf-connecting-ip", "true-client-ip",
                 "tailscale-user-login", "x-forwarded-host")


# ── Password ───────────────────────────────────────────────────────────────

def hash_password(password: str, iterations: int | None = None) -> str:
    iterations = iterations or ITERATIONS
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt, digest = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        attempt = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
    except ValueError:
        return False
    return hmac.compare_digest(attempt.hex(), digest)


def has_password(conn: sqlite3.Connection) -> bool:
    return bool(db.get_setting(conn, HASH_KEY))


def set_password(conn: sqlite3.Connection, password: str) -> None:
    """Also rotates the signing secret, so every session signed before this one ends."""
    if not password:
        raise ValueError("The password can't be empty.")
    db.set_setting(conn, HASH_KEY, hash_password(password))
    db.set_setting(conn, SECRET_KEY, secrets.token_hex(32))


def clear_password(conn: sqlite3.Connection) -> None:
    db.set_setting(conn, HASH_KEY, "")
    db.set_setting(conn, SECRET_KEY, secrets.token_hex(32))


def verify(conn: sqlite3.Connection, password: str) -> bool:
    stored = db.get_setting(conn, HASH_KEY)
    return bool(stored) and check_password(password, stored)


# ── Sessions ───────────────────────────────────────────────────────────────

def _secret(conn: sqlite3.Connection) -> bytes:
    secret = db.get_setting(conn, SECRET_KEY)
    if not secret:
        secret = secrets.token_hex(32)
        db.set_setting(conn, SECRET_KEY, secret)
    return bytes.fromhex(secret)


def _sign(conn: sqlite3.Connection, expires: int) -> str:
    return hmac.new(_secret(conn), f"dm.{expires}".encode(), hashlib.sha256).hexdigest()


def issue(conn: sqlite3.Connection, now: float | None = None) -> str:
    expires = int((now or time.time()) + SESSION_DAYS * 86400)
    return f"{expires}.{_sign(conn, expires)}"


def valid(conn: sqlite3.Connection, token: str | None, now: float | None = None) -> bool:
    if not token or "." not in token:
        return False
    expires, signature = token.split(".", 1)
    if not expires.isdigit() or int(expires) < (now or time.time()):
        return False
    return hmac.compare_digest(_sign(conn, int(expires)), signature)


def trusted_host(request: HTTPConnection) -> bool:
    """The machine running the server, reached directly (no proxy or tunnel in between)."""
    host = request.client.host if request.client else ""
    return host in LOOPBACK and not any(h in request.headers for h in PROXY_HEADERS)


def is_dm(conn: sqlite3.Connection, request: HTTPConnection) -> bool:
    if valid(conn, request.cookies.get(COOKIE)):
        return True
    return not has_password(conn) and trusted_host(request)


# ── What a player may reach ────────────────────────────────────────────────

PARTY_PAGES = {"/", "/search", "/login", "/view/party"}


def party_safe(method: str, path: str) -> bool:
    """The allowlist: read-only pages that render party view when the visitor isn't the DM.
    Each of these routes already filters by party knowledge; everything else is DM-only."""
    if path == "/login":
        return method in ("GET", "HEAD", "POST")
    if method not in ("GET", "HEAD"):
        return False
    if path in PARTY_PAGES or path.startswith("/static/"):
        return True
    parts = path.strip("/").split("/")
    if len(parts) == 2 and parts[0] == "portrait" and parts[1].isdigit():
        return True
    return parts[0] in ENTITY_TYPES and (len(parts) == 1 or (len(parts) == 2 and parts[1].isdigit()))


# ── Command line ───────────────────────────────────────────────────────────

def main(argv: list[str]) -> int:
    command = argv[0] if argv else ""
    db.init_db()
    with closing(db.get_connection()) as conn:
        if command == "set-password":
            first = getpass.getpass("New DM password: ")
            if not first:
                print("Empty password; nothing changed.")
                return 1
            if getpass.getpass("Again: ") != first:
                print("They don't match; nothing changed.")
                return 1
            set_password(conn, first)
            print(f"DM password set for {db.db_path()}. Every earlier session is signed out.")
            return 0
        if command == "clear-password":
            clear_password(conn)
            print("DM password removed. Only this machine, reached directly, is treated as the DM now.")
            return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
