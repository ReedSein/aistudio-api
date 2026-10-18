"""Normalize a CloakBrowser tarball without exposing partial installations."""

from __future__ import annotations

import argparse
import tarfile
import tempfile
from pathlib import Path


def unpack(archive: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise ValueError(f"目标目录已存在，未覆盖：{destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Stage next to the final directory so rename stays atomic across filesystems.
    with tempfile.TemporaryDirectory(
        prefix=".install-", dir=destination.parent
    ) as directory:
        stage = Path(directory)
        with tarfile.open(archive, "r:gz") as bundle:
            bundle.extractall(stage, filter="data")
        candidates = [
            p.parent
            for p in stage.rglob("chrome")
            if p.is_file() and (p.parent / "icudtl.dat").is_file()
        ]
        if len(candidates) != 1:
            raise ValueError("浏览器归档须包含唯一的 chrome 和 icudtl.dat。")
        root = candidates[0]
        chrome = root / "chrome"
        chrome.chmod(chrome.stat().st_mode | 0o111)
        if root == stage:
            normalized = stage / "normalized"
            normalized.mkdir()
            for child in stage.iterdir():
                if child != normalized:
                    child.rename(normalized / child.name)
            root = normalized
        root.rename(destination)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    unpack(args.archive, args.destination)


if __name__ == "__main__":
    main()
