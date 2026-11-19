import re
from contextlib import closing

import pytest
from fastapi.routing import APIRoute
from starlette.requests import Request

from src.app import auth, db
from src.app.main import create_app


def make_entity(conn, **kw):
    args = dict(type_key="npc", name="Someone", fields={}, status="canon", known=False)
    args.update(kw)
    return db.create_entity(conn, args["type_key"], args["name"], args["fields"], args["status"], args["known"])


def fake_request(host="127.0.0.1", headers=None, cookies=""):
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    if cookies:
        raw.append((b"cookie", cookies.encode()))
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw, "client": (host, 5000)})


# ── Passwords and sessions ─────────────────────────────────────────────────

def test_password_round_trip(conn):
    assert not auth.has_password(conn)
    auth.set_password(conn, "hunter2")
    assert auth.verify(conn, "hunter2")
    assert not auth.verify(conn, "hunter3")
    assert "hunter2" not in db.get_setting(conn, auth.HASH_KEY)


def test_empty_password_is_refused(conn):
    with pytest.raises(ValueError):
        auth.set_password(conn, "")


def test_tokens_expire_and_resist_tampering(conn):
    token = auth.issue(conn, now=1_000)
    assert auth.valid(conn, token, now=2_000)
    assert not auth.valid(conn, token, now=1_000 + auth.SESSION_DAYS * 86400 + 1)
    expires, sig = token.split(".")
    assert not auth.valid(conn, f"{int(expires) + 999}.{sig}", now=2_000)  # stretched expiry
    assert not auth.valid(conn, f"{expires}.{'0' * len(sig)}", now=2_000)
    for junk in ("", "nonsense", ".", "abc.def"):
        assert not auth.valid(conn, junk)


def test_a_new_password_signs_everyone_out(conn):
    token = auth.issue(conn)
    auth.set_password(conn, "first")
    assert not auth.valid(conn, token)
    token = auth.issue(conn)
    auth.set_password(conn, "second")
    assert not auth.valid(conn, token)


# ── Who counts as the DM ───────────────────────────────────────────────────

def test_without_a_password_only_this_machine_is_the_dm(conn):
    assert auth.is_dm(conn, fake_request("127.0.0.1"))
    assert auth.is_dm(conn, fake_request("::1"))
    assert not auth.is_dm(conn, fake_request("192.168.1.40"))


@pytest.mark.parametrize("header", ["X-Forwarded-For", "CF-Connecting-IP", "Forwarded", "X-Real-IP"])
def test_a_tunnel_arriving_from_loopback_is_not_trusted(conn, header):
    assert not auth.is_dm(conn, fake_request("127.0.0.1", {header: "203.0.113.9"}))


def test_with_a_password_even_this_machine_signs_in(conn):
    auth.set_password(conn, "pw")
    assert not auth.is_dm(conn, fake_request("127.0.0.1"))
    assert auth.is_dm(conn, fake_request("192.168.1.40", cookies=f"{auth.COOKIE}={auth.issue(conn)}"))


# ── The gate ───────────────────────────────────────────────────────────────

def test_players_see_party_view_and_cannot_ask_for_the_dm_view(player):
    with closing(db.get_connection()) as conn:
        known = make_entity(conn, name="Known Face", known=True, fields={"description": "Open. ||Hidden truth.||"})
        unknown = make_entity(conn, name="Shadow")
    player.get("/view/dm")  # the old hole: this used to hand anyone the DM view
    page = player.get(f"/npc/{known}")
    assert page.status_code == 200 and "Known Face" in page.text
    assert "Hidden truth" not in page.text
    assert 'class="view-toggle"' not in page.text
    assert "/drafts" not in page.text
    assert player.get(f"/npc/{unknown}").status_code == 404
    assert "Shadow" not in player.get("/npc").text
    assert f"/npc/{unknown}" not in player.get("/search", params={"q": "Shadow"}).text  # (the query itself echoes)


def test_players_cannot_change_anything(player):
    with closing(db.get_connection()) as conn:
        known = make_entity(conn, name="Known Face", known=True)
        hidden = make_entity(conn, name="Shadow")
    for path, data in [(f"/npc/{hidden}/known", {"known": "1"}), (f"/npc/{known}/status", {"status": "archived"}),
                       (f"/npc/{known}/links", {"to_id": str(hidden)}), (f"/npc/{known}", {"name": "Renamed"}),
                       (f"/npc/{known}/discard", {})]:
        assert player.post(path, data=data).status_code == 403, path
    with closing(db.get_connection()) as conn:
        assert not db.get_entity(conn, hidden)["known_to_party"]
        assert db.get_entity(conn, known)["name"] == "Known Face"
        assert db.get_entity(conn, known)["status"] == "canon"


def test_dm_pages_send_players_to_sign_in(player):
    response = player.get("/drafts", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/login?next=/drafts"
    assert player.get("/drafts", headers={"HX-Request": "true"}).status_code == 403


def test_signing_in_and_out(player):
    with closing(db.get_connection()) as conn:
        auth.set_password(conn, "correct horse")
    wrong = player.post("/login", data={"password": "nope", "next": "/drafts"})
    assert wrong.status_code == 401
    right = player.post("/login", data={"password": "correct horse", "next": "/drafts"}, follow_redirects=False)
    assert right.status_code == 303 and right.headers["location"] == "/drafts"
    assert player.get("/drafts").status_code == 200
    assert 'class="view-toggle"' in player.get("/").text
    player.post("/logout")
    assert player.get("/drafts", follow_redirects=False).status_code == 303


@pytest.mark.parametrize("target", ["//evil.example", "https://evil.example", "/\\evil.example"])
def test_login_never_redirects_off_site(player, target):
    with closing(db.get_connection()) as conn:
        auth.set_password(conn, "pw")
    response = player.post("/login", data={"password": "pw", "next": target}, follow_redirects=False)
    assert response.headers["location"] == "/"


def test_every_route_is_dm_only_unless_allowlisted(player):
    """Deny by default, checked over the whole app: a new route is DM-only until it's allowlisted on purpose."""
    with closing(db.get_connection()) as conn:
        entity_id = make_entity(conn, known=True)
    samples = {"type_key": "npc", "entity_id": str(entity_id)}
    checked = 0
    for route in create_app().routes:
        if not isinstance(route, APIRoute):
            continue
        path = re.sub(r"\{(\w+)(?::\w+)?\}", lambda m: samples.get(m.group(1), "1"), route.path)
        for method in route.methods - {"HEAD"}:
            if auth.party_safe(method, path):
                continue
            response = player.request(method, path, follow_redirects=False)
            if method == "GET":
                assert response.status_code == 303 and response.headers["location"].startswith("/login"), path
            else:
                assert response.status_code == 403, f"{method} {path}"
            checked += 1
    assert checked > 40  # the sweep really ran over the app's routes


def test_the_allowlist_is_read_only():
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        for path in ("/", "/search", "/npc", "/npc/1", "/portrait/1", "/view/party"):
            assert not auth.party_safe(method, path)
    for path in ("/npc/new", "/npc/1/edit", "/npc/1/history", "/drafts", "/generate", "/encounters/new", "/view/dm"):
        assert not auth.party_safe("GET", path)
