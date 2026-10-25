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
import re
import stat
import sys
import tarfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE: Final = ROOT / "infra" / "docker-compose.gpu.yml"
# The GPU-free remote-AI overlay rides in the same bundle so the signed release
# path can deploy it for a CPU-only production host. It declares no relative
# bind mounts of its own today, but its sources are collected the same way the
# production topology's are, so any it adds later ship automatically.
REMOTE_AI_OVERLAY: Final = ROOT / "infra" / "docker-compose.remote-ai.yml"


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


class _ComposeLoader(yaml.SafeLoader):
    """SafeLoader that accepts Compose's merge tags (!override, !reset)."""


def _compose_tag(loader: yaml.SafeLoader, _suffix: str, node: yaml.Node) -> object:
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return loader.construct_scalar(node)


_ComposeLoader.add_multi_constructor("!", _compose_tag)


def _mount_source(volume: object) -> str | None:
    """The host path of a relative bind mount, in short or long syntax."""
    if isinstance(volume, dict):
        source = volume.get("source") if volume.get("type", "bind") == "bind" else None
    elif isinstance(volume, str):
        source = _short_syntax_source(volume)
    else:
        source = None
    source = _default_relative_path(source) if isinstance(source, str) else source
    if isinstance(source, str) and source.startswith(("./", "../")):
        return source
    return None


def _short_syntax_source(volume: str) -> str:
    """The SOURCE field of a ``SOURCE:TARGET[:MODE]`` short-syntax volume.

    A leading ``${VAR:-default}`` interpolation contains its own colon, so the
    source is taken up to the first colon that falls outside a ``${...}`` group
    rather than the first colon overall.
    """
    if volume.startswith("${"):
        end = volume.find("}")
        if end != -1:
            return volume[: end + 1]
    return volume.split(":", 1)[0]


def _default_relative_path(source: str) -> str:
    """Resolve a ``${VAR:-./path}`` mount source to its static default path.

    The production topology mounts the inference registry as
    ``${AI_REGISTRY_DIR:-./ai-registry}`` so an operator can point it elsewhere;
    the deterministic release bundle carries the shipped default so the registry
    (and the GPU-free overlay that depends on it) deploys from the archive. A
    bare host path or any other form is returned unchanged.
    """
    match = re.fullmatch(r"\$\{[A-Za-z_][A-Za-z0-9_]*:-(\.\.?/[^}]*)\}", source)
    return match.group(1) if match else source


def compose_bind_mount_sources(compose_file: Path = COMPOSE_FILE) -> list[Path]:
    """Return static relative host paths referenced by Compose bind mounts.

    Mounts are read from the parsed file, so block lists, flow lists
    (`volumes: [./a:/b]`) and long-syntax entries are all found.
    """
    compose = yaml.load(compose_file.read_text(encoding="utf-8"), Loader=_ComposeLoader)
    sources = {compose_file.resolve()}
    for service in (compose.get("services") or {}).values():
        for volume in (service or {}).get("volumes") or []:
            relative = _mount_source(volume)
            if relative is None:
                continue
            source = (compose_file.parent / relative).resolve()
            _relative_to_root(source)
            if not source.exists():
                raise ValueError(f"Compose bind-mount source does not exist: {relative}")
            sources.add(source)
    return sorted(sources, key=lambda path: _relative_to_root(path))


def collect_bundle_files(
    compose_file: Path = COMPOSE_FILE, overlay_file: Path | None = REMOTE_AI_OVERLAY
) -> list[BundleFile]:
    files: list[BundleFile] = []
    seen: set[str] = set()
    sources = list(compose_bind_mount_sources(compose_file))
    if overlay_file is not None:
        _relative_to_root(overlay_file.resolve())
        if not overlay_file.is_file():
            raise ValueError(f"GPU-free overlay does not exist: {overlay_file}")
        # The overlay file and any relative bind mounts it declares (none today).
        for source in compose_bind_mount_sources(overlay_file):
            if source not in sources:
                sources.append(source)
    for source in sorted(sources, key=lambda path: _relative_to_root(path)):
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
