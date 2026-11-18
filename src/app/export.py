"""Export the party view as a static site: the players' field-book as plain files for a free static host.

It walks the app's own party view from the front page, following only links the party can see, so it
can only ever export what party view shows. Pages land as <path>/index.html (hosts serve those for
/<path>), with the stylesheet and the portraits of entries the party can see. What makes no sense on a
static host goes: the DM/Party switch, search, HTMX. A last check refuses to write anything carrying a
DM-only marker.

    python -m src.app.export [out_dir]      (default: data/export, next to the bible)
"""
import re
import shutil
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from . import db, portraits
from .main import APP_DIR, create_app

PAGE_LINK = re.compile(r'href="(/(?:[a-z]+(?:/\d+)?)?)"')
PORTRAIT = re.compile(r'src="/portrait/(\d+)"')
STRIP = [
    re.compile(r'<form class="site-search".*?</form>', re.S),
    re.compile(r'<div class="view-toggle".*?</div>', re.S),
    re.compile(r'<script src="/static/htmx\.min\.js"[^>]*></script>'),
]
# Anything only the DM view renders. If one turns up, something leaked: stop rather than publish it.
DM_MARKERS = ('class="secret', "tease-dm", "dm-tag", "reveal-passage", "passage-tool", "/view/dm",
              "Drafts to review", "review-panel")


class ExportLeak(RuntimeError):
    pass


def clean(html: str) -> str:
    for pattern in STRIP:
        html = pattern.sub("", html)
    return html


def export(out: Path) -> dict:
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    pages, portrait_ids = {}, set()
    with TestClient(create_app()) as client:
        client.get("/view/party")
        queue, seen = ["/"], set()
        while queue:
            path = queue.pop(0)
            if path in seen:
                continue
            seen.add(path)
            response = client.get(path)
            if response.status_code != 200:
                continue  # an entry the party can't see: not exported, never linked
            html = clean(response.text)
            leaks = [m for m in DM_MARKERS if m in html]
            if leaks:
                raise ExportLeak(f"{path} carries DM-only markup ({', '.join(leaks)}); nothing was published.")
            portrait_ids.update(int(i) for i in PORTRAIT.findall(html))
            pages[path] = html
            queue.extend(link for link in PAGE_LINK.findall(html) if link not in seen)

    found = {}
    for entity_id in sorted(portrait_ids):
        source = portraits.find(entity_id)
        if source is not None:
            (out / "portraits").mkdir(exist_ok=True)
            shutil.copyfile(source, out / "portraits" / f"{entity_id}{source.suffix}")
            found[entity_id] = f"/portraits/{entity_id}{source.suffix}"
    for path, html in pages.items():
        html = PORTRAIT.sub(lambda m: f'src="{found.get(int(m.group(1)), "")}"', html)
        target = out / path.strip("/") / "index.html" if path != "/" else out / "index.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html, encoding="utf-8")
    shutil.copytree(APP_DIR / "static", out / "static", ignore=shutil.ignore_patterns("htmx*"))
    return {"pages": len(pages), "portraits": len(found), "out": str(out)}


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else db.db_path().parent / "export"
    result = export(target)
    print(f"Exported {result['pages']} pages and {result['portraits']} portraits to {result['out']}")
