#!/usr/bin/env python3
"""Build a deterministic Compose release bundle from its declared bind mounts.

The archive inventory is derived from the production Compose file rather than a
hand-maintained tar argument list.  A canonical JSON layout is emitted alongside
the archive and is consumed by the signed release-manifest generator.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import stat
import sys
import tarfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE: Final = ROOT / "infra" / "docker-compose.gpu.yml"
BIND_MOUNT_RE: Final = re.compile(r"^\s*-\s+((?:\./|\.\./)[^:\s]+):[^\s]+")


@dataclass(frozen=True)
class BundleFile:
    path: str
    sha256: str
    size_bytes: int
    mode: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_to_root(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError as error:
        raise ValueError(f"release input escapes repository root: {path}") from error


def compose_bind_mount_sources(compose_file: Path = COMPOSE_FILE) -> list[Path]:
    """Return static relative host paths referenced by Compose bind mounts."""
    sources = {compose_file.resolve()}
    for raw_line in compose_file.read_text(encoding="utf-8").splitlines():
        match = BIND_MOUNT_RE.match(raw_line)
        if not match:
            continue
        source = (compose_file.parent / match.group(1)).resolve()
        _relative_to_root(source)
        if not source.exists():
            raise ValueError(f"Compose bind-mount source does not exist: {match.group(1)}")
        sources.add(source)
    return sorted(sources, key=lambda path: _relative_to_root(path))


def collect_bundle_files(compose_file: Path = COMPOSE_FILE) -> list[BundleFile]:
    files: list[BundleFile] = []
    seen: set[str] = set()
    for source in compose_bind_mount_sources(compose_file):
        candidates = [source] if source.is_file() else sorted(source.rglob("*"))
        for candidate in candidates:
            if candidate.is_dir():
                continue
            if candidate.is_symlink() or not candidate.is_file():
                raise ValueError(f"release inputs must be regular files: {candidate}")
            relative = _relative_to_root(candidate.resolve())
            if relative in seen:
                continue
            seen.add(relative)
            metadata = candidate.stat()
            files.append(
                BundleFile(
                    path=relative,
                    sha256=_sha256(candidate),
                    size_bytes=metadata.st_size,
                    mode=stat.S_IMODE(metadata.st_mode),
                )
            )
    return sorted(files, key=lambda item: item.path)


def build_bundle(*, output: Path, files: list[BundleFile]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for item in files:
                    source = ROOT / item.path
                    info = tarfile.TarInfo(name=item.path)
                    info.size = item.size_bytes
                    info.mode = item.mode
                    info.mtime = 0
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    with source.open("rb") as payload:
                        archive.addfile(info, payload)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="output .tar.gz path")
    parser.add_argument(
        "--layout-output", type=Path, required=True, help="canonical per-file layout JSON"
    )
    parser.add_argument(
        "--compose-file", type=Path, default=COMPOSE_FILE, help="production Compose topology"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    compose_file = args.compose_file.resolve()
    if compose_file != COMPOSE_FILE.resolve():
        _relative_to_root(compose_file)
    files = collect_bundle_files(compose_file)
    if not any(item.path.startswith("infra/temporal/") for item in files):
        raise ValueError("release bundle must include infra/temporal runtime scripts")
    build_bundle(output=args.output, files=files)
    args.layout_output.parent.mkdir(parents=True, exist_ok=True)
    args.layout_output.write_text(
        json.dumps([asdict(item) for item in files], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, tarfile.TarError) as error:
        print(f"release bundle error: {error}", file=sys.stderr)
        raise SystemExit(2) from error
