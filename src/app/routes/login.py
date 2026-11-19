"""Signing the DM in and out. The gate itself (who may reach what) lives in main.py and auth.py."""
import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from .. import auth
from .entities import VIEW_COOKIE, Conn, render

router = APIRouter()


def safe_next(target: str) -> str:
    """Only same-site paths, so the login form can't become an open redirect."""
    return target if target.startswith("/") and not target.startswith("//") and "\\" not in target else "/"


def login_page(request: Request, conn, next_: str, error: str | None = None, status_code: int = 200):
    return render(request, "login.html", {
        "next": safe_next(next_), "error": error, "has_password": auth.has_password(conn),
    }, status_code)


@router.get("/login")
async def login_form(request: Request, conn: Conn, next: str = "/"):
    return login_page(request, conn, next)


@router.post("/login")
async def login(request: Request, conn: Conn):
    form = await request.form()
    target = safe_next(str(form.get("next", "/")))
    if not auth.has_password(conn):
        return login_page(request, conn, target, "No DM password is set yet.", 409)
    if not auth.verify(conn, str(form.get("password", ""))):
        await asyncio.sleep(1)  # a guessing script gets one try a second, and this box isn't a bank
        return login_page(request, conn, target, "That's not it.", 401)
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(auth.COOKIE, auth.issue(conn), max_age=auth.SESSION_DAYS * 86400,
                        httponly=True, samesite="lax")
    response.set_cookie(VIEW_COOKIE, "dm", max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax")
    return response


@router.post("/logout")
async def logout():
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(auth.COOKIE)
    return response
