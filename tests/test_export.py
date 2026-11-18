from contextlib import closing

import pytest

from src.app import db, export, portraits

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def test_export_holds_only_what_the_party_sees(client, tmp_path):
    with closing(db.get_connection()) as conn:
        mara = db.create_entity(conn, "npc", "Mara", {"hook": "Rows. ||Smuggles relics.||", "voice": "Whispers."},
                                known=True)
        hidden = db.create_entity(conn, "npc", "Kreil", {"hook": "Spymaster."})
        place = db.create_entity(conn, "location", "The ford", {}, known=True)
        db.add_link(conn, mara, place, "works at", known=True)
        db.add_link(conn, mara, hidden, "reports to")  # a link the party doesn't know
    portraits.save(mara, "image/png", PNG)
    portraits.save(hidden, "image/png", PNG)

    result = export.export(tmp_path / "site")
    site = tmp_path / "site"
    everything = "".join(p.read_text(encoding="utf-8") for p in site.rglob("*.html"))
    assert (site / "index.html").exists() and (site / "npc" / str(mara) / "index.html").exists()
    assert (site / "location" / str(place) / "index.html").exists() and (site / "static" / "style.css").exists()
    assert not (site / "npc" / str(hidden)).exists()
    for secret in ("Smuggles", "Whispers", "Kreil", "Spymaster", "/view/dm", "site-search"):
        assert secret not in everything, secret
    assert (site / "portraits" / f"{mara}.png").exists() and not (site / "portraits" / f"{hidden}.png").exists()
    assert f'src="/portraits/{mara}.png"' in everything
    assert result["portraits"] == 1


def test_a_leak_stops_the_export(client, tmp_path, monkeypatch):
    with closing(db.get_connection()) as conn:
        db.create_entity(conn, "npc", "Mara", {}, known=True)
    monkeypatch.setattr(export, "clean", lambda html: html + '<span class="secret">oops</span>')
    with pytest.raises(export.ExportLeak):
        export.export(tmp_path / "site")
