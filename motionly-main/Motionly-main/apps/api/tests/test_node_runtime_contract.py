"""Node.js runtime contract (Decision D1: Node 24 LTS everywhere).

Node 20 reached end of life on 2026-04-30. Every image, CI job, type package,
and engines declaration must agree on Node 24, image pins must be well-formed
digests, and no runtime may depend on the experimental ``--loader`` flag.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
NODE_MAJOR = "24"
NODE_IMAGE = re.compile(r"^FROM\s+node:(\S+?)@sha256:([0-9a-f]+)", re.MULTILINE)
COPY_FROM_NODE = re.compile(r"^COPY\s+--from=node:(\S+?)@sha256:([0-9a-f]+)\s", re.MULTILINE)
ANY_DIGEST = re.compile(r"@sha256:([0-9a-f]+)")
PACKAGE_MANIFESTS = (
    "package.json",
    "apps/web/package.json",
    "apps/worker/package.json",
    "apps/renderer/package.json",
    "apps/api/worker-bridge/package.json",
    "packages/shared/package.json",
)


def _dockerfiles() -> list[Path]:
    return [
        ROOT / "Dockerfile",
        *sorted((ROOT / "apps").glob("*/Dockerfile*")),
        ROOT / "infra/temporal/server/Dockerfile",
    ]


def test_every_image_digest_pin_is_a_full_sha256() -> None:
    for path in _dockerfiles():
        for digest in ANY_DIGEST.findall(path.read_text(encoding="utf-8")):
            assert len(digest) == 64, f"{path.relative_to(ROOT)} has a malformed digest {digest}"


def test_node_base_images_are_node_24_and_digest_pinned() -> None:
    node_images = []
    for path in _dockerfiles():
        source = path.read_text(encoding="utf-8")
        node_images += [(path, tag) for tag, _ in NODE_IMAGE.findall(source)]
        node_images += [(path, tag) for tag, _ in COPY_FROM_NODE.findall(source)]
    assert node_images, "expected at least one Node base image"
    for path, tag in node_images:
        assert tag.startswith(f"{NODE_MAJOR}-"), f"{path.relative_to(ROOT)} uses node:{tag}"


def test_python_images_take_node_from_the_pinned_node_24_image_not_apt() -> None:
    for path in _dockerfiles():
        source = path.read_text(encoding="utf-8")
        if not re.search(r"^FROM\s+python:", source, re.MULTILINE):
            continue
        uses_node = "npm ci" in source or "node src/" in source or "worker-bridge" in source
        if not uses_node:
            continue
        relative = path.relative_to(ROOT)
        apt_lines = [line for line in source.splitlines() if "apt-get install" in line]
        assert not any(re.search(r"\bnodejs\b|\bnpm\b", line) for line in apt_lines), (
            f"{relative} must not install Debian's nodejs/npm (Node 20 on trixie)"
        )
        assert COPY_FROM_NODE.search(source), f"{relative} must copy Node 24 from node:24"


def test_ci_workflows_pin_node_24() -> None:
    for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        version = (workflow.get("env") or {}).get("NODE_VERSION")
        if version is not None:
            assert str(version) == NODE_MAJOR, f"{path.name} NODE_VERSION={version}"
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                if "actions/setup-node" in str(step.get("uses", "")):
                    pinned = str((step.get("with") or {}).get("node-version", ""))
                    assert pinned in {NODE_MAJOR, "${{ env.NODE_VERSION }}"}, path.name


def test_packages_declare_node_24_engines_and_types() -> None:
    for relative in PACKAGE_MANIFESTS:
        manifest = json.loads((ROOT / relative).read_text(encoding="utf-8"))
        engines = (manifest.get("engines") or {}).get("node")
        assert engines == f">={NODE_MAJOR} <{int(NODE_MAJOR) + 1}", f"{relative} engines={engines}"
        types = (manifest.get("devDependencies") or {}).get("@types/node")
        if types is not None:
            assert types.startswith(f"{NODE_MAJOR}."), f"{relative} @types/node={types}"


def test_no_runtime_uses_the_experimental_loader_flag() -> None:
    checked = [
        ROOT / "apps/renderer/package.json",
        ROOT / "apps/worker/src/remotion.mjs",
        ROOT / "apps/renderer/src/smoke-render.ts",
        ROOT / "apps/worker/Dockerfile",
        ROOT / "scripts/verify/stages.json",
    ]
    for path in checked:
        source = path.read_text(encoding="utf-8")
        assert "--loader" not in source, f"{path.relative_to(ROOT)} still uses --loader"
        assert '"--loader"' not in source
    renderer = json.loads((ROOT / "apps/renderer/package.json").read_text(encoding="utf-8"))
    for name in ("render", "render:cli", "render:smoke", "test", "test:encoded", "test:golden"):
        assert "--import ./ts-node-register.mjs" in renderer["scripts"][name], name
    register = (ROOT / "apps/renderer/ts-node-register.mjs").read_text(encoding="utf-8")
    assert 'register("ts-node/esm", pathToFileURL("./"))' in register


def test_docker_npm_lockfiles_resolve_every_package_from_the_registry() -> None:
    """`npm ci` inside an image only sees the build context. A lockfile entry
    that links into the pnpm store (``../../node_modules/.pnpm/...``) installs
    nothing there, so the worker crashed with "Cannot find package 'bullmq'"."""
    for relative in ("apps/worker/package-lock.json", "apps/api/worker-bridge/package-lock.json"):
        lock = json.loads((ROOT / relative).read_text(encoding="utf-8"))
        manifest = json.loads((ROOT / relative).with_name("package.json").read_text("utf-8"))
        packages = lock["packages"]
        for name, version in manifest.get("dependencies", {}).items():
            entry = packages.get(f"node_modules/{name}")
            assert entry, f"{relative} does not lock {name}"
            locked = entry.get("version")
            assert locked == version, f"{relative} locks {name}@{locked}"
        for key, entry in packages.items():
            if key == "":
                continue
            assert not entry.get("link"), f"{relative} links {key} outside the build context"
            assert key.startswith("node_modules/"), f"{relative} has non-registry entry {key}"
            assert str(entry.get("resolved", "")).startswith("https://registry.npmjs.org/"), (
                f"{relative}: {key} is not resolved from the npm registry"
            )
            assert str(entry.get("integrity", "")).startswith("sha512-"), f"{relative}: {key}"


def test_playwright_is_pinned_to_a_release_that_runs_on_node_24() -> None:
    """@playwright/test before 1.60 hangs on Node 24 while loading ESM specs
    (and while installing browsers), so `playwright test` never starts."""
    package = json.loads((ROOT / "apps" / "web" / "package.json").read_text(encoding="utf-8"))
    version = package["devDependencies"]["@playwright/test"]

    assert re.fullmatch(r"\d+\.\d+\.\d+", version), f"pin an exact version, not {version!r}"
    assert tuple(int(part) for part in version.split(".")) >= (1, 60, 0), version
