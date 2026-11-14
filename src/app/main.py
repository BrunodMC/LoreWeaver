from contextlib import asynccontextmanager, closing
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import db, generation
from .routes import entities, generate, review

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
    app.include_router(generate.router)  # first: the entity routes' /{type_key} would swallow /generate
    app.include_router(review.router)
    app.include_router(entities.router)
    return app


app = create_app()
