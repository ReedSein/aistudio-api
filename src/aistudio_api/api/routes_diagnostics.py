"""Protected diagnostics status and local support archive download."""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from aistudio_api.infrastructure.diagnostics.bundle import export_bundle
from aistudio_api.infrastructure.diagnostics.store import DiagnosticError, get_store
from aistudio_api.infrastructure.utils.logger import get_logger

from .diagnostics_client import BrowserReport, read_client_report

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])
logger = get_logger("diagnostics")


class DiagnosticsStatus(BaseModel):
    directory: str
    retention_days: int
    max_bytes: int
    file_count: int | None
    total_bytes: int | None
    oldest_file_time: str | None
    collection_error: str | None
    contains_conversation_content: bool


@router.get("", response_model=DiagnosticsStatus)
def diagnostics_status() -> dict[str, object]:
    return get_store().status()


@router.post("/export", response_class=FileResponse)
async def download_diagnostics(request: Request) -> FileResponse:
    report = await read_client_report(request)
    return await run_in_threadpool(create_download, report)


def create_download(report: BrowserReport | None = None) -> FileResponse:
    from aistudio_api.config import settings

    if report is not None and not get_store().emit(
        "client_diagnostic_report",
        evidence_source="unverified_browser_report",
        client_report=report.model_dump(mode="json", exclude_none=True),
    ):
        raise HTTPException(503, detail="浏览器诊断摘要未能保存，请先导出本地记录。")
    directory = tempfile.TemporaryDirectory(prefix="aistudio-diagnostics-")
    archive = Path(directory.name) / "aistudio-diagnostics.tar.gz"
    try:
        manifest = export_bundle(
            archive,
            config_file=Path(settings.config_file),
            legacy_dirs=(Path(settings.dump_dir),),
        )
        logger.info(
            "诊断包已生成，文件数=%s，存在缺失=%s",
            manifest["file_count"],
            manifest["has_omissions"],
        )
    except (OSError, ValueError, DiagnosticError) as exc:
        directory.cleanup()
        logger.exception("诊断包生成失败")
        raise HTTPException(
            500, detail=f"无法生成诊断包 ({type(exc).__name__})；请尝试本地导出。"
        ) from exc
    except BaseException:
        directory.cleanup()
        raise
    return FileResponse(
        archive,
        media_type="application/gzip",
        filename=archive.name,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        background=BackgroundTask(directory.cleanup),
    )
