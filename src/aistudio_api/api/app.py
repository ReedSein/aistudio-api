"""FastAPI application entrypoint."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from aistudio_api.application.api_service_gemini import classify_gemini_error_payload
from aistudio_api.infrastructure.gateway.client import AIStudioClient
from aistudio_api.infrastructure.gateway.model_defaults import (
    get_configured_logging_settings,
)
from aistudio_api.infrastructure.utils.logger import (
    get_logger,
    setup_logging,
)

from .dependencies import require_api_key, require_web_auth
from .diagnostics_middleware import DiagnosticsMiddleware
from .routes_accounts import router as accounts_router
from .routes_diagnostics import router as diagnostics_router
from .routes_gemini import router as gemini_router
from .routes_models import router as models_router
from .routes_system import (
    protected_router as system_protected_router,
    public_router as system_public_router,
)
from .routes_userscript import router as userscript_router
from .state import runtime_state

_log_cfg = get_configured_logging_settings()
setup_logging(str(_log_cfg.get("level", "INFO")))
logger = get_logger("server")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from aistudio_api.application.account_rotator import init_rotator
    from aistudio_api.application.account_service import AccountService
    from aistudio_api.config import settings
    from aistudio_api.infrastructure.account.account_store import AccountStore
    from aistudio_api.infrastructure.browser.browser_engine import (
        install_process_cleanup_handlers,
    )
    from aistudio_api.infrastructure.gateway.model_defaults import (
        apply_configured_runtime_settings,
    )

    # 先应用 config.yaml runtime 段（环境变量显式设置时优先），再启动后台任务
    apply_configured_runtime_settings(settings)

    install_process_cleanup_handlers()

    client = AIStudioClient(
        port=runtime_state.browser_port,
    )
    runtime_state.client = client

    # 初始化账号管理服务
    account_store = AccountStore()
    account_service = AccountService(account_store)
    runtime_state.account_service = account_service

    # 初始化黏性账号调度器
    rotator = init_rotator(account_store)
    runtime_state.rotator = rotator

    logger.info(
        "Client initialized (port=%s, accounts=%d)",
        runtime_state.browser_port,
        len(account_store.list_accounts()),
    )
    logger.info("控制台地址: http://localhost:%s/", runtime_state.port)

    # 后台预热浏览器，避免首次请求延迟
    warmup_task = None

    async def _warmup():
        for attempt in range(3):
            try:
                await client.warmup()
                return
            except Exception as e:
                logger.debug(
                    "浏览器预热第 %d 次失败: %s", attempt + 1, e, exc_info=True
                )
                if attempt < 2:
                    await asyncio.sleep(1.0)
                    continue
                logger.warning("浏览器预热失败: %s", e)

    warmup_task = asyncio.create_task(_warmup())

    idle_task = None
    if settings.browser_idle_timeout > 0:

        async def _idle_monitor():
            while True:
                await asyncio.sleep(10.0)
                try:
                    if runtime_state.client and getattr(
                        runtime_state.client, "_session", None
                    ):
                        await runtime_state.client._session.check_idle_timeout()
                except Exception:
                    logger.debug("浏览器空闲检查失败", exc_info=True)

        idle_task = asyncio.create_task(_idle_monitor())

    yield
    logger.info("服务正在关闭")
    if warmup_task and not warmup_task.done():
        warmup_task.cancel()
    if idle_task and not idle_task.done():
        idle_task.cancel()
    if client:
        try:
            await client.close()
        except Exception as e:
            logger.debug("关闭客户端失败: %s", e)
    runtime_state.client = None
    runtime_state.account_service = None
    runtime_state.rotator = None


app = FastAPI(title="AI Studio API", lifespan=lifespan)
app.include_router(system_public_router)
app.include_router(system_protected_router, dependencies=[Depends(require_web_auth)])
app.include_router(
    accounts_router, prefix="/api", dependencies=[Depends(require_web_auth)]
)
app.include_router(accounts_router, dependencies=[Depends(require_web_auth)])
app.include_router(
    diagnostics_router, prefix="/api", dependencies=[Depends(require_web_auth)]
)
app.include_router(gemini_router, dependencies=[Depends(require_api_key)])
app.include_router(models_router, dependencies=[Depends(require_api_key)])
app.include_router(userscript_router)


@app.exception_handler(HTTPException)
async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
    code, msg, status_str = classify_gemini_error_payload(exc)
    return JSONResponse(
        status_code=code,
        content={
            "error": {
                "code": code,
                "message": msg,
                "status": status_str,
            }
        },
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    code, msg, status_str = 400, str(exc), "INVALID_ARGUMENT"
    return JSONResponse(
        status_code=code,
        content={
            "error": {
                "code": code,
                "message": msg,
                "status": status_str,
            }
        },
    )


# 挂载前端静态资源与 SPA 路由支持
static_dir = Path(__file__).resolve().parents[1] / "static"
index_html_path = static_dir / "index.html"

if static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    assets_dir = static_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")


@app.get("/auth/check")
async def auth_check():
    """检查认证状态，用于前端判断是否需要登录。"""
    from aistudio_api.config import settings

    return {"auth_enabled": settings.auth_enabled}


@app.middleware("http")
async def spa_navigation_middleware(request: Request, call_next):
    """统一处理前端 SPA 单页应用导航：浏览器页面直达/刷新响应 index.html，API 请求直通 JSON。"""
    accept = request.headers.get("accept", "")
    is_html_nav = (
        request.method == "GET"
        and "text/html" in accept
        and "application/json" not in accept
    )
    is_api = request.url.path.startswith(
        (
            "/api",
            "/v1beta",
            "/auth",
            "/assets",
            "/static",
            "/health",
            "/stats",
            "/rotation",
            "/config",
            "/api-keys",
            "/userscript",
            "/version",
        )
    )
    is_accounts_spa = request.url.path in ("/accounts", "/accounts/")
    if is_html_nav and (is_accounts_spa or not is_api) and index_html_path.is_file():
        response = FileResponse(index_html_path)
        response.headers["Vary"] = "Accept"
        response.headers["Cache-Control"] = (
            "no-store, no-cache, must-revalidate, max-age=0"
        )
        return response

    response = await call_next(request)

    if request.url.path.startswith(("/accounts", "/api/accounts")) or is_html_nav:
        existing_vary = response.headers.get("Vary")
        if existing_vary:
            if "Accept" not in [v.strip() for v in existing_vary.split(",")]:
                response.headers["Vary"] = f"{existing_vary}, Accept"
        else:
            response.headers["Vary"] = "Accept"
        response.headers["Cache-Control"] = (
            "no-store, no-cache, must-revalidate, max-age=0"
        )

    return response


@app.get("/")
async def root_index():
    """根路径兜底响应。"""
    if index_html_path.is_file():
        response = FileResponse(index_html_path)
        response.headers["Vary"] = "Accept"
        response.headers["Cache-Control"] = (
            "no-store, no-cache, must-revalidate, max-age=0"
        )
        return response
    return JSONResponse({"message": "AI Studio API Web UI"})


app.add_middleware(DiagnosticsMiddleware)


def main():
    from aistudio_api.config import settings

    parser = argparse.ArgumentParser(description="AI Studio API Server")
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("--browser-port", type=int, default=settings.browser_port)
    args = parser.parse_args()

    runtime_state.browser_port = args.browser_port
    runtime_state.port = args.port

    import uvicorn

    logger.info("正在启动服务，监听端口 %s", args.port)
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="info")
