"""Dependency-free access to the shared diagnostics core and offline exporter."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

# The offline entry point locates the local package without an installed environment.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aistudio_api.infrastructure.diagnostics.bundle import export_bundle
from aistudio_api.infrastructure.diagnostics.process import install_script_logging
from aistudio_api.infrastructure.diagnostics.store import DiagnosticError, get_store

PROJECT_ROOT = Path(__file__).resolve().parents[1]
__all__ = ["export_logs", "get_store", "install_script_logging"]


def export_logs(root: Path, output: Path | None = None) -> Path:
    if output is None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        output = Path.home() / f"aistudio-diagnostics-{stamp}-{os.getpid()}.tar.gz"
    manifest = export_bundle(output, root=root)
    print(f"诊断包已保存：{output}\n包含对话原文，请只发送给信任的排错人员。")
    if manifest["has_omissions"]:
        print("部分证据未能收集，原因已写入包内 manifest.json。")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="不依赖应用虚拟环境的诊断包导出")
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        export_logs(args.project_root, args.output)
    except (OSError, ValueError, DiagnosticError) as exc:
        print(f"诊断包导出失败 ({type(exc).__name__})。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
