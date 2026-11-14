from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import db
from .routes import entities, generate, review

APP_DIR = Path(__file__).parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.backup_db()  # before migrations, so a failed migration leaves a pre-migration snapshot
    db.init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="LoreWeaver", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
    app.include_router(generate.router)  # first: the entity routes' /{type_key} would swallow /generate
    app.include_router(review.router)
    app.include_router(entities.router)
    return app


app = create_app()
