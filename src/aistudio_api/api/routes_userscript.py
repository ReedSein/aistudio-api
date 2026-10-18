"""油猴脚本托管路由（公开只读）。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

router = APIRouter(prefix="/userscript")

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[3] / "userscript" / "aistudio-cookie.user.js"
)
_SELF_URL_PLACEHOLDER = "__AISTUDIO_SELF_URL__"


@router.get("/aistudio-cookie.user.js")
async def serve_cookie_userscript(request: Request) -> Response:
    """托管 Cookie 抓取脚本；@updateURL / @downloadURL 按实际访问地址改写。"""
    if not _SCRIPT_PATH.is_file():
        raise HTTPException(status_code=404, detail="脚本文件不存在")
    content = _SCRIPT_PATH.read_text(encoding="utf-8").replace(
        _SELF_URL_PLACEHOLDER, str(request.url)
    )
    return Response(
        content=content,
        media_type="application/javascript; charset=utf-8",
        headers={"Cache-Control": "no-cache"},
    )
