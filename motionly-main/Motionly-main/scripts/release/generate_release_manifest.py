#!/usr/bin/env python3
"""Generate and validate Motionly's immutable, archive-bound Compose release manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "infra" / "release-manifest.schema.json"
COMPOSE_FILE: Final = "infra/docker-compose.gpu.yml"
BUNDLE_NAME: Final = "release-bundle.tar.gz"
BUILD_COMPONENTS: Final = (
    "api",
    "web",
    "cpu-worker",
    "gpu-worker",
    "demucs-worker",
    "remotion-worker",
    "temporal-server",
)
IMAGE_COMPONENT_BY_VARIABLE: Final = {
    "MOTIONLY_API_IMAGE": "api",
    "MOTIONLY_WEB_IMAGE": "web",
    "MOTIONLY_CPU_FFMPEG_WORKER_IMAGE": "cpu-worker",
    "MOTIONLY_GPU_WORKER_IMAGE": "gpu-worker",
    "MOTIONLY_GPU_FFMPEG_WORKER_IMAGE": "cpu-worker",
    "MOTIONLY_DEMUCS_WORKER_IMAGE": "demucs-worker",
    "MOTIONLY_PROVIDER_WORKER_IMAGE": "cpu-worker",
    "MOTIONLY_PLANNING_WORKER_IMAGE": "cpu-worker",
    "MOTIONLY_REMOTION_WORKER_IMAGE": "remotion-worker",
    "MOTIONLY_TEMPORAL_SERVER_IMAGE": "temporal-server",
}
# The GPU-free remote-AI overlay (infra/docker-compose.remote-ai.yml) adds one
# optional CPU vision worker image and its SBOM. A manifest may carry the pair
# or omit it, but never a half of it; everything else stays required.
OPTIONAL_IMAGE_COMPONENT_BY_VARIABLE: Final = {
    "MOTIONLY_CPU_VISION_WORKER_IMAGE": "cpu-vision-worker",
}
OPTIONAL_BUILD_COMPONENTS: Final = ("cpu-vision-worker",)
# GHCR, or the loopback registry of the local release rehearsal
# (scripts/release/rehearse_release.py), which only a rehearsal host accepts.
DIGEST_IMAGE_RE: Final = re.compile(
    r"^(?:ghcr\.io|127\.0\.0\.1:\d{2,5}|localhost:\d{2,5})/[a-z0-9][a-z0-9._/-]*@sha256:[a-f0-9]{64}$"
)
REVISION_RE: Final = re.compile(r"^[0-9a-f]{7,64}$")
SHA256_RE: Final = re.compile(r"^[a-f0-9]{64}$")
SBOM_ARTIFACT_RE: Final = re.compile(r"^[A-Za-z0-9._-]+\.spdx\.json$")
BUNDLE_PATH_RE: Final = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[A-Za-z0-9._/-]+$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assignments(values: list[str], kind: str) -> dict[str, str]:
    assignments: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"{kind} must use NAME=VALUE, got {value!r}")
        name, assigned_value = value.split("=", 1)
        if not name or not assigned_value or name in assignments:
            raise ValueError(f"invalid or duplicate {kind} assignment {value!r}")
        assignments[name] = assigned_value
    return assignments


def _validate_common(manifest: dict[str, object]) -> None:
    if not isinstance(manifest.get("revision"), str) or not REVISION_RE.fullmatch(manifest["revision"]):
        raise ValueError("release manifest revision must be a lowercase Git SHA")
    if not isinstance(manifest.get("created_at"), str):
        raise ValueError("release manifest created_at must be an RFC3339 timestamp")
    try:
        datetime.fromisoformat(manifest["created_at"].replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("release manifest created_at must be an RFC3339 timestamp") from error
    if manifest.get("compose_file") != COMPOSE_FILE:
        raise ValueError(f"release manifest compose_file must be {COMPOSE_FILE}")

    images = manifest.get("images")
    if not isinstance(images, dict):
        raise ValueError("release manifest must define exactly every Compose image variable")
    required_images = set(IMAGE_COMPONENT_BY_VARIABLE)
    optional_images = set(OPTIONAL_IMAGE_COMPONENT_BY_VARIABLE)
    extra_images = set(images) - required_images
    if not required_images <= set(images) or not extra_images <= optional_images:
        raise ValueError("release manifest must define exactly every Compose image variable")
    for variable, image in images.items():
        if not isinstance(image, str) or not DIGEST_IMAGE_RE.fullmatch(image):
            raise ValueError(f"{variable} must be a digest-pinned GHCR image")

    sboms = manifest.get("sboms")
    if not isinstance(sboms, dict):
        raise ValueError("release manifest must define an SBOM for every built component")
    required_sboms = set(BUILD_COMPONENTS)
    optional_sboms = set(OPTIONAL_BUILD_COMPONENTS)
    extra_sboms = set(sboms) - required_sboms
    if not required_sboms <= set(sboms) or not extra_sboms <= optional_sboms:
        raise ValueError("release manifest must define an SBOM for every built component")
    for component, artifact in sboms.items():
        if not isinstance(artifact, str) or not SBOM_ARTIFACT_RE.fullmatch(artifact):
            raise ValueError(f"{component} SBOM must be a local SPDX JSON artifact name")

    # The optional CPU-vision image and its SBOM travel together or not at all.
    has_optional_image = bool(extra_images)
    has_optional_sbom = bool(extra_sboms)
    if has_optional_image != has_optional_sbom:
        raise ValueError(
            "release manifest optional cpu-vision-worker image and SBOM must be present together"
        )


def _validate_file_entries(entries: object) -> list[dict[str, object]]:
    if not isinstance(entries, list) or not entries:
        raise ValueError("release manifest bundle.files must be a non-empty list")
    files: list[dict[str, object]] = []
    paths: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "size_bytes", "mode"}:
            raise ValueError("release manifest bundle file entry is invalid")
        path = entry["path"]
        digest = entry["sha256"]
        size = entry["size_bytes"]
        mode = entry["mode"]
        if not isinstance(path, str) or not BUNDLE_PATH_RE.fullmatch(path):
            raise ValueError("release manifest bundle file path is invalid")
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise ValueError("release manifest bundle file digest is invalid")
        if not isinstance(size, int) or size < 0:
            raise ValueError("release manifest bundle file size is invalid")
        if not isinstance(mode, int) or not 0 <= mode <= 0o777:
            raise ValueError("release manifest bundle file mode is invalid")
        files.append(entry)
        paths.append(path)
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise ValueError("release manifest bundle files must be sorted and unique")
    return files


def _validate_v1_manifest(manifest: dict[str, object]) -> dict[str, object]:
    expected = {"schema_version", "revision", "created_at", "compose_file", "images", "sboms"}
    if set(manifest) != expected or manifest.get("schema_version") != 1:
        raise ValueError("release manifest schema_version 1 is invalid")
    _validate_common(manifest)
    return manifest


def _validate_v2_manifest(manifest: dict[str, object]) -> dict[str, object]:
    expected = {
        "schema_version",
        "release_id",
        "revision",
        "created_at",
        "compose_file",
        "images",
        "sboms",
        "bundle",
    }
    if set(manifest) != expected or manifest.get("schema_version") != 2:
        raise ValueError("release manifest schema_version 2 is invalid")
    _validate_common(manifest)
    if manifest.get("release_id") != manifest.get("revision"):
        raise ValueError("release manifest release_id must equal the immutable revision")
    bundle = manifest.get("bundle")
    if not isinstance(bundle, dict) or set(bundle) != {"name", "sha256", "size_bytes", "files"}:
        raise ValueError("release manifest bundle is invalid")
    if bundle["name"] != BUNDLE_NAME:
        raise ValueError(f"release manifest bundle name must be {BUNDLE_NAME}")
    if not isinstance(bundle["sha256"], str) or not SHA256_RE.fullmatch(bundle["sha256"]):
        raise ValueError("release manifest bundle digest is invalid")
    if not isinstance(bundle["size_bytes"], int) or bundle["size_bytes"] <= 0:
        raise ValueError("release manifest bundle size is invalid")
    _validate_file_entries(bundle["files"])
    return manifest


def validate_manifest(manifest: object, *, allow_v1: bool = True) -> dict[str, object]:
    if not isinstance(manifest, dict):
        raise ValueError("release manifest must be an object")
    version = manifest.get("schema_version")
    if version == 2:
        return _validate_v2_manifest(manifest)
    if version == 1 and allow_v1:
        return _validate_v1_manifest(manifest)
    raise ValueError("release manifest schema_version must be 2")


def _load_layout(path: Path) -> list[dict[str, object]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return _validate_file_entries(data)


def build_manifest(
    *,
    revision: str,
    created_at: str,
    images: dict[str, str],
    sboms: dict[str, str],
    bundle_path: Path,
    bundle_layout: list[dict[str, object]],
) -> dict[str, object]:
    return validate_manifest(
        {
            "schema_version": 2,
            "release_id": revision,
            "revision": revision,
            "created_at": created_at,
            "compose_file": COMPOSE_FILE,
            "images": images,
            "sboms": sboms,
            "bundle": {
                "name": BUNDLE_NAME,
                "sha256": _sha256(bundle_path),
                "size_bytes": bundle_path.stat().st_size,
                "files": bundle_layout,
            },
        },
        allow_v1=False,
    )


def _safe_tar_name(name: str) -> str:
    if not BUNDLE_PATH_RE.fullmatch(name):
        raise ValueError("release bundle contains an unsafe path")
    return name


def _archive_files(bundle_path: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    with tarfile.open(bundle_path, mode="r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            name = _safe_tar_name(member.name)
            if not member.isfile() or member.issym() or member.islnk() or member.isdev():
                raise ValueError("release bundle may contain regular files only")
            source = archive.extractfile(member)
            if source is None:
                raise ValueError("release bundle contains unreadable member")
            digest = hashlib.sha256()
            size = 0
            with source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    size += len(chunk)
                    digest.update(chunk)
            entries.append(
                {
                    "path": name,
                    "sha256": digest.hexdigest(),
                    "size_bytes": size,
                    "mode": member.mode & 0o777,
                }
            )
    return _validate_file_entries(sorted(entries, key=lambda entry: str(entry["path"])))


def validate_bundle(manifest: object, bundle_path: Path) -> None:
    checked = validate_manifest(manifest, allow_v1=False)
    bundle = checked["bundle"]
    assert isinstance(bundle, dict)
    if bundle_path.name != bundle["name"]:
        raise ValueError("release bundle filename does not match signed manifest")
    if bundle_path.stat().st_size != bundle["size_bytes"]:
        raise ValueError("release bundle byte count does not match signed manifest")
    if _sha256(bundle_path) != bundle["sha256"]:
        raise ValueError("release bundle digest does not match signed manifest")
    actual = _archive_files(bundle_path)
    if actual != bundle["files"]:
        raise ValueError("release bundle files do not match signed manifest")
    if not any(entry["path"] == COMPOSE_FILE for entry in actual):
        raise ValueError("release bundle lacks production Compose configuration")
    if not any(str(entry["path"]).startswith("infra/temporal/") for entry in actual):
        raise ValueError("release bundle lacks Temporal runtime inputs")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", help="lowercase Git commit SHA")
    parser.add_argument("--image", action="append", default=[], help="Compose variable=GHCR digest")
    parser.add_argument("--sbom", action="append", default=[], help="component=SPDX artifact name")
    parser.add_argument("--created-at", help="RFC3339 timestamp; defaults to current UTC time")
    parser.add_argument("--bundle", type=Path, help="deterministic release-bundle.tar.gz")
    parser.add_argument("--bundle-layout", type=Path, help="canonical bundle file layout JSON")
    parser.add_argument("--output", type=Path, help="manifest output path")
    parser.add_argument("--validate", type=Path, help="validate an existing manifest")
    parser.add_argument("--validate-bundle", action="store_true", help="validate an archive against --manifest")
    parser.add_argument("--manifest", type=Path, help="manifest for --validate-bundle")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.validate:
        if args.validate_bundle or args.manifest:
            raise ValueError("--validate cannot be combined with bundle validation")
        validate_manifest(json.loads(args.validate.read_text(encoding="utf-8")))
        return 0
    if args.validate_bundle:
        if not args.manifest or not args.bundle:
            raise ValueError("--validate-bundle requires --manifest and --bundle")
        validate_bundle(json.loads(args.manifest.read_text(encoding="utf-8")), args.bundle)
        return 0
    if not args.revision or not args.output or not args.bundle or not args.bundle_layout:
        raise ValueError("--revision, --output, --bundle, and --bundle-layout are required")
    if not args.bundle.is_file() or args.bundle.name != BUNDLE_NAME:
        raise ValueError(f"--bundle must be an existing {BUNDLE_NAME}")
    created_at = args.created_at or datetime.now(UTC).isoformat().replace("+00:00", "Z")
    manifest = build_manifest(
        revision=args.revision,
        created_at=created_at,
        images=_assignments(args.image, "image"),
        sboms=_assignments(args.sbom, "SBOM"),
        bundle_path=args.bundle,
        bundle_layout=_load_layout(args.bundle_layout),
    )
    validate_bundle(manifest, args.bundle)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError, tarfile.TarError) as error:
        print(f"release manifest error: {error}", file=sys.stderr)
        raise SystemExit(2) from error
