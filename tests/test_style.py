from src.app.entity_types import ENTITY_TYPES, NAV_GROUPS


def test_every_type_sits_in_exactly_one_nav_group():
    grouped = [t for *_, types in NAV_GROUPS for t in types]
    assert sorted(grouped) == sorted(ENTITY_TYPES)


def test_each_view_wears_its_own_look(client):
    page = client.get("/").text
    assert 'class="theme-archive"' in page and "LORE<span>·</span>WEAVER" in page and 'id="lamp"' in page
    for _, dm_label, _, _ in NAV_GROUPS:
        assert f"<summary>{dm_label}</summary>" in page
    client.get("/view/party")
    page = client.get("/").text
    assert 'class="theme-codex party-view"' in page and "Perils" in page and "Drafts to review" not in page
