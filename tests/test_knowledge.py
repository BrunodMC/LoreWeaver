from src.app import knowledge


def test_party_text_strips_secrets():
    text = "A great owl with twin eyes. ||The Reaping Eye was stolen.|| She watches the seasons."
    assert knowledge.party_text(text) == "A great owl with twin eyes. She watches the seasons."


def test_whole_field_secret_leaves_nothing():
    assert knowledge.party_text("||Tall, gaunt, silver-eyed.||") == ""


def test_unclosed_marker_fails_closed():
    assert knowledge.has_unclosed_secret("Public. ||Secret without end")
    assert knowledge.party_text("Public. ||Secret without end") == "Public."


def test_dm_render_highlights_secrets_and_escapes_html():
    html = str(knowledge.render("<b>x</b> ||<script>bad</script>||"))
    assert "&lt;b&gt;x&lt;/b&gt;" in html
    assert '<span class="secret"' in html and "&lt;script&gt;bad&lt;/script&gt;" in html
    assert "<script>" not in html


def test_party_render_escapes_and_omits_secrets():
    html = str(knowledge.render("<i>hi</i> ||secret||", party_view=True))
    assert html == "&lt;i&gt;hi&lt;/i&gt;"
