"""The Briefing page: what wants the GM's attention before the next session."""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .. import briefing
from .entities import Conn, render
from .generate import dm_only

router = APIRouter()


@router.get("/briefing", response_class=HTMLResponse)
async def briefing_page(request: Request, conn: Conn):
    dm_only(request)
    return render(request, "briefing.html", {"brief": briefing.gather(conn)})
