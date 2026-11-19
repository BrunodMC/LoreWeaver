from contextlib import asynccontextmanager, closing
from pathlib import Path

from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import auth, db, generation
from .routes import audits, briefing, encounters, entities, generate, login, portraits, proposals, review

APP_DIR = Path(__file__).parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.backup_db()  # before migrations, so a failed migration leaves a pre-migration snapshot
    db.init_db()
    with closing(db.get_connection()) as conn:
        generation.move_legacy_notes(conn)  # one-off data fix, idempotent; cheap on a small bible
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="LoreWeaver", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")

    @app.middleware("http")
    async def dm_gate(request: Request, call_next):
        """Deny by default: a visitor without a DM session gets party view, and only the party-safe pages."""
        if request.url.path.startswith("/static/"):
            return await call_next(request)
        with closing(db.get_connection()) as conn:
            request.state.is_dm = auth.is_dm(conn, request)
        if not request.state.is_dm and not auth.party_safe(request.method, request.url.path):
            if request.method in ("GET", "HEAD") and not request.headers.get("HX-Request"):
                here = request.url.path + (f"?{request.url.query}" if request.url.query else "")
                return RedirectResponse(f"/login?next={quote(here)}", status_code=303)
            return PlainTextResponse("Only the DM can do that.", status_code=403)
        return await call_next(request)

    app.include_router(login.router)  # before the entity routes: /login would look like a type
    app.include_router(generate.router)  # first: the entity routes' /{type_key} would swallow /generate
    app.include_router(encounters.router)  # likewise /encounters (the encounter type itself lives at /encounter)
    app.include_router(review.router)
    app.include_router(briefing.router)  # before the entity routes: /briefing would look like a type
    app.include_router(audits.router)
    app.include_router(proposals.router)
    app.include_router(portraits.router)  # before the entity routes: /portrait/{id} would look like a type
    app.include_router(entities.router)
    return app


app = create_app()
