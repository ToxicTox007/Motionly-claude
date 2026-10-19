#!/usr/bin/env python3
"""Rehearse the AWS release path locally (`pnpm verify --stage aws`).

Runs the real release agent (scripts/deploy/apply_release.sh) the way the
instance runs it, against stand-ins for AWS that are test infrastructure only:

- moto (motoserver/moto) serves S3 and SSM Parameter Store;
- a local registry (registry:3) holds the CPU-stage images this checkout built
  (scripts/verify/cpu_stage_stack.py build), pushed and referenced by digest;
- releases are signed with a throwaway Cosign key, not GitHub's keyless
  identity;
- the CPU-stage overlay replaces GPU capabilities with labelled fixtures, and
  the CloudFront simulator stands in for CloudFront.

It publishes releases exactly as .github/workflows/deploy-aws.yml does
(conditional writes, manifest last), then:

1. preflights and applies release 1 and checks the public origin;
2. applies release 2 and checks that current/previous moved;
3. applies release 3, broken so that it never becomes healthy, and checks that
   the agent restored release 2 and the origin still answers;

and always tears everything down. Writes test-results/aws/rehearsal.json.
Nothing here talks to AWS.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STACK = json.loads((ROOT / "tests" / "cpu-stage" / "stack.json").read_text(encoding="utf-8"))
RESULTS = ROOT / "test-results" / "aws"
TOOLS = Path(os.environ.get("MOTIONLY_AWS_TOOLS", "/tmp/motionly-aws-tools"))

MOTO_IMAGE = (
    "motoserver/moto:5.1.14@sha256:800719627036dc38dc699e9f314f624f2c98ac3d8bb754db078cb2698f08ee77"
)
REGISTRY_IMAGE = (
    "registry:3.0.0@sha256:6c5666b861f3505b116bb9aa9b25175e71210414bd010d92035ff64018f9457e"
)
MOTO_PORT = 15000
REGISTRY = "127.0.0.1:15001"
CONTAINERS = ("motionly-rehearsal-moto", "motionly-rehearsal-registry")
BUCKET = "motionly-rehearsal-releases"
ENVIRONMENT = "rehearsal"
REGION = "ap-south-1"
PUBLIC_ORIGIN = STACK["public_origin"]
PROJECT = "motionly"
OVERLAY = ROOT / "infra" / "docker-compose.cpu-stage.yml"

# Compose image variable -> CPU-stage image this checkout built.
COMPONENT_IMAGES = {
    "api": "motionly-api:cpu-stage",
    "web": "motionly-web:cpu-stage",
    "cpu-worker": "motionly-cpu-worker:cpu-stage",
    "remotion-worker": "motionly-remotion-worker:cpu-stage",
    "temporal-server": "motionly-temporal-server:cpu-stage",
}
IMAGE_VARIABLES = {
    "MOTIONLY_API_IMAGE": "api",
    "MOTIONLY_WEB_IMAGE": "web",
    "MOTIONLY_CPU_FFMPEG_WORKER_IMAGE": "cpu-worker",
    "MOTIONLY_GPU_FFMPEG_WORKER_IMAGE": "cpu-worker",
    "MOTIONLY_PROVIDER_WORKER_IMAGE": "cpu-worker",
    "MOTIONLY_PLANNING_WORKER_IMAGE": "cpu-worker",
    # The CPU stage runs demucs-worker's queue on cpu-worker (tests/cpu-stage/stack.json).
    "MOTIONLY_DEMUCS_WORKER_IMAGE": "cpu-worker",
    "MOTIONLY_REMOTION_WORKER_IMAGE": "remotion-worker",
    "MOTIONLY_TEMPORAL_SERVER_IMAGE": "temporal-server",
    # GPU stage only: never started with ComputeProfile=cpu (fixtures/registry.json).
    "MOTIONLY_GPU_WORKER_IMAGE": "gpu-stage-placeholder",
}
SBOMS = (
    "api",
    "web",
    "cpu-worker",
    "gpu-worker",
    "demucs-worker",
    "remotion-worker",
    "temporal-server",
)
# Files the CPU-stage overlay mounts that are not instance secrets in production.
EXTRA_SECRETS = ("edge-tls-cert", "edge-tls-key")
# Secret files whose values are multi-line and travel base64-encoded.
_BASE64 = re.compile(r"^(temporal_.*_(cert|key)|edge_tls_(cert|key))$")
_PARAMETER_NAMES = {
    "edge_origin_verify": "edge/origin-verify",
    "edge_origin_verify_previous": "edge/origin-verify-previous",
}
_NOT_SHIPPED = {"edge_ca_cert"}


class RehearsalError(RuntimeError):
    pass


def log(message: str) -> None:
    print(f"rehearsal: {message}", flush=True)


def new_release_id() -> str:
    return secrets.token_hex(20)


def parameters_from_inputs(secrets_dir: Path, environment: dict[str, str]) -> dict[str, str]:
    """SSM parameters (name under /motionly/<environment>/ -> value) for the
    generated test secrets, named and encoded as the agent reads them."""
    parameters: dict[str, str] = {"supabase-anon-key": environment["SUPABASE_ANON_KEY"]}
    for path in sorted(secrets_dir.iterdir()):
        if path.name in _NOT_SHIPPED:
            continue
        name = _PARAMETER_NAMES.get(path.name, path.name.replace("_", "-"))
        if _BASE64.match(path.name):
            parameters[name] = base64.b64encode(path.read_bytes()).decode("ascii")
            continue
        value = path.read_text(encoding="utf-8").strip()
        if value:  # SSM stores no empty values; the agent treats these as optional.
            parameters[name] = value
    return parameters


_MOUNT = re.compile(r"^(\s+- )((?:\./|\.\./)[^:\s]+)(:.*)$")


def rehearsal_overlay(overlay_text: str, bundle_paths: set[str], checkout: Path) -> str:
    """Compose resolves the overlay's relative mounts against the release
    bundle. Keep those the bundle carries; point the CPU-stage test
    infrastructure (simulator, its contract copy, the CPU-stage Caddyfile) at
    the checkout."""
    lines = []
    for line in overlay_text.splitlines():
        match = _MOUNT.match(line)
        if match:
            source = (checkout / "infra" / match.group(2)).resolve()
            relative = source.relative_to(checkout).as_posix()
            carried = relative in bundle_paths or any(
                path.startswith(relative + "/") for path in bundle_paths
            )
            if not carried:
                line = f"{match.group(1)}{source}{match.group(3)}"
        lines.append(line)
    return "\n".join(lines) + ("\n" if overlay_text.endswith("\n") else "")


def run(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    check: bool = True,
    capture: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command, env=env, text=True, capture_output=capture, input=input_text, check=False
    )
    if check and result.returncode != 0:
        raise RehearsalError(
            f"{' '.join(command[:4])} failed ({result.returncode}):\n"
            f"{(result.stdout or '')[-2000:]}{(result.stderr or '')[-2000:]}"
        )
    return result


class Rehearsal:
    def __init__(self, work: Path) -> None:
        self.work = work
        self.aws_env = {
            **os.environ,
            "AWS_ACCESS_KEY_ID": "rehearsal",
            "AWS_SECRET_ACCESS_KEY": "rehearsal",
            "AWS_DEFAULT_REGION": REGION,
            "AWS_REGION": REGION,
            "AWS_ENDPOINT_URL_S3": f"http://127.0.0.1:{MOTO_PORT}",
            "AWS_ENDPOINT_URL_SSM": f"http://127.0.0.1:{MOTO_PORT}",
            "PATH": f"{TOOLS / 'venv' / 'bin'}:{TOOLS}:{os.environ['PATH']}",
        }
        self.images: dict[str, str] = {}
        self.report: dict[str, object] = {"steps": []}

    def aws(self, *arguments: str, input_text: str | None = None) -> str:
        return run(["aws", *arguments], env=self.aws_env, input_text=input_text).stdout

    def step(self, name: str, **details: object) -> None:
        log(name)
        self.report["steps"].append({"step": name, **details})

    # --- stand-ins for AWS ---------------------------------------------------
    def start_emulators(self) -> None:
        # The registry image declares a volume; `rm --volumes` (here and in
        # teardown) removes it with the container instead of leaving the pushed
        # images on the Docker host's disk.
        for name in CONTAINERS:
            run(["docker", "rm", "-f", "--volumes", name], check=False)
        run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                CONTAINERS[0],
                "-p",
                f"127.0.0.1:{MOTO_PORT}:5000",
                MOTO_IMAGE,
            ]
        )
        run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                CONTAINERS[1],
                "-p",
                f"{REGISTRY}:5000",
                REGISTRY_IMAGE,
            ]
        )
        for _ in range(60):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{MOTO_PORT}/moto-api/", timeout=2)
                urllib.request.urlopen(f"http://{REGISTRY}/v2/", timeout=2)
                break
            except OSError:
                time.sleep(1)
        else:
            raise RehearsalError("moto or the registry did not start")
        self.aws(
            "s3api",
            "create-bucket",
            "--bucket",
            BUCKET,
            "--create-bucket-configuration",
            f"LocationConstraint={REGION}",
        )
        self.step("started moto (S3, SSM) and a local registry")

    def push_images(self) -> None:
        for component, local in COMPONENT_IMAGES.items():
            if run(["docker", "image", "inspect", local], check=False).returncode != 0:
                raise RehearsalError(
                    f"{local} is missing; run scripts/verify/cpu_stage_stack.py build"
                )
            self.images[component] = self._push(local, component)
        self.images["gpu-stage-placeholder"] = self._push_placeholder_image()
        self.step("pushed the CPU-stage images by digest", images=self.images)

    def _push(self, local: str, component: str) -> str:
        remote = f"{REGISTRY}/motionly/{component}:rehearsal"
        run(["docker", "tag", local, remote])
        run(["docker", "push", "-q", remote])
        digests = json.loads(
            run(["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", remote]).stdout
        )
        digest = next(d for d in digests if d.startswith(f"{REGISTRY}/motionly/{component}@"))
        run(["docker", "rmi", remote], check=False)
        return digest

    def _push_placeholder_image(self) -> str:
        """An image with no executable, standing in for a GPU-stage image that
        ComputeProfile=cpu never starts, and for the broken release 3."""
        context = self.work / "placeholder"
        context.mkdir()
        (context / "PLACEHOLDER.txt").write_text(
            "Release rehearsal placeholder: not a Motionly image. It has no executable.\n",
            encoding="utf-8",
        )
        (context / "Dockerfile").write_text(
            "FROM scratch\nCOPY PLACEHOLDER.txt /\n"
            'LABEL org.motionly.rehearsal="placeholder, never deployed"\n',
            encoding="utf-8",
        )
        run(["docker", "build", "-q", "-t", "motionly-rehearsal-placeholder:local", str(context)])
        digest = self._push("motionly-rehearsal-placeholder:local", "gpu-stage-placeholder")
        run(["docker", "rmi", "motionly-rehearsal-placeholder:local"], check=False)
        return digest

    def store_parameters(self) -> None:
        inputs = self.work / "inputs"
        run(
            [
                sys.executable,
                str(ROOT / "scripts" / "release" / "write_compose_test_env.py"),
                "--cpu-stage",
                str(ROOT / "tests" / "cpu-stage" / "stack.json"),
                "--env-file",
                str(inputs / "compose.env"),
                "--secrets-dir",
                str(inputs / "secrets"),
            ]
        )
        environment = dict(
            line.split("=", 1)
            for line in (inputs / "compose.env").read_text().splitlines()
            if "=" in line
        )
        parameters = parameters_from_inputs(inputs / "secrets", environment)
        for name, value in parameters.items():
            # The value goes in through a file, never as an argument.
            value_file = self.work / "parameter.value"
            value_file.write_text(value, encoding="utf-8")
            self.aws(
                "ssm",
                "put-parameter",
                "--name",
                f"/motionly/{ENVIRONMENT}/{name}",
                "--type",
                "SecureString",
                "--value",
                f"file://{value_file}",
            )
            value_file.unlink()
        self.ca_cert = inputs / "secrets" / "edge_ca_cert"
        self.step("stored test secrets in SSM Parameter Store", parameters=sorted(parameters))

    # --- the host ------------------------------------------------------------
    def prepare_host(self) -> None:
        agent = self.work / "agent" / "bin"
        agent.mkdir(parents=True)
        for source, name in (
            ("scripts/deploy/apply_release.sh", "apply-release"),
            ("scripts/release/generate_release_manifest.py", "generate-release-manifest"),
            ("scripts/deploy/temporal_canary_gate.sh", "temporal-canary-gate"),
        ):
            shutil.copy2(ROOT / source, agent / name)
            (agent / name).chmod(0o755)
        shutil.copy2(ROOT / "infra" / "release-manifest.schema.json", self.work / "agent")
        env = {**self.aws_env, "COSIGN_PASSWORD": ""}
        run(
            ["cosign", "generate-key-pair", "--output-key-prefix", str(self.work / "cosign")],
            env=env,
        )
        proof = self.work / "temporal-canary-proof"
        proof.write_text(
            "# Release rehearsal only: no live Temporal canary was run. These lines\n"
            "# satisfy the gate's format so the rest of the release path can be rehearsed.\n"
            + "".join(
                f"{gate}=true\n"
                for gate in (
                    "signed_release_verified",
                    "preflight_verified",
                    "mtls_handshake_verified",
                    "authorization_role_matrix_verified",
                    "controlled_namespace_validation_verified",
                    "rollback_verified",
                    "dual_frontend_overlap_verified",
                )
            ),
            encoding="utf-8",
        )
        layout = json.loads(self._bundle(self.work / "layout-probe"))
        overlay = self.work / "overlay" / "docker-compose.cpu-stage.yml"
        overlay.parent.mkdir()
        overlay.write_text(
            rehearsal_overlay(
                OVERLAY.read_text(encoding="utf-8"), {f["path"] for f in layout}, ROOT
            ),
            encoding="utf-8",
        )
        self.host_env = self.work / "host.env"
        until = (datetime.now(UTC) + timedelta(hours=3)).replace(microsecond=0)
        values = {
            "MOTIONLY_RELEASE_ROOT": self.work / "releases",
            "MOTIONLY_AGENT_ROOT": self.work / "agent",
            "MOTIONLY_ENVIRONMENT": ENVIRONMENT,
            "MOTIONLY_AWS_REGION": REGION,
            "MOTIONLY_RELEASE_BUCKET": BUCKET,
            "MOTIONLY_COMPUTE_PROFILE": "cpu",
            "MOTIONLY_REGISTRY": REGISTRY,
            "MOTIONLY_REGISTRY_USERNAME": "",
            "MOTIONLY_APP_DOMAIN": PUBLIC_ORIGIN.removeprefix("https://"),
            "MOTIONLY_COSIGN_IDENTITY_REGEXP": "^unused-in-rehearsal$",
            "MOTIONLY_COSIGN_PUBLIC_KEY": self.work / "cosign.pub",
            "MOTIONLY_COMPOSE_OVERLAY": overlay,
            "MOTIONLY_REHEARSAL_EXTRA_SECRETS": '"' + " ".join(EXTRA_SECRETS) + '"',
            "MOTIONLY_TEMPORAL_ADMIN_TOOLS_IMAGE": STACK["temporal_admin_tools_image"],
            "MOTIONLY_TEMPORAL_CANARY_OVERLAP_PROOF_FILE": proof,
            "MOTIONLY_TEMPORAL_BOOTSTRAP_ADMIN_UNTIL": until.isoformat().replace("+00:00", "Z"),
        }
        self.host_env.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
        self.host_env.chmod(0o600)
        self.overlay = overlay
        self.step("installed the agent with a rehearsal host configuration")

    def _bundle(self, directory: Path) -> str:
        directory.mkdir(parents=True)
        run(
            [
                sys.executable,
                str(ROOT / "scripts" / "release" / "package_release_bundle.py"),
                "--output",
                str(directory / "release-bundle.tar.gz"),
                "--layout-output",
                str(directory / "release-bundle.layout.json"),
            ]
        )
        return (directory / "release-bundle.layout.json").read_text(encoding="utf-8")

    # --- publishing, as deploy-aws.yml does ----------------------------------
    def publish(self, release_id: str, *, images: dict[str, str] | None = None) -> None:
        images = images or self.images
        directory = self.work / "publish" / release_id
        self._bundle(directory)
        arguments = [
            sys.executable,
            str(ROOT / "scripts" / "release" / "generate_release_manifest.py"),
            "--revision",
            release_id,
            "--bundle",
            str(directory / "release-bundle.tar.gz"),
            "--bundle-layout",
            str(directory / "release-bundle.layout.json"),
            "--output",
            str(directory / "release-manifest.json"),
        ]
        for variable, component in IMAGE_VARIABLES.items():
            arguments += ["--image", f"{variable}={images[component]}"]
        for component in SBOMS:
            arguments += ["--sbom", f"{component}=sbom-{component}.spdx.json"]
        run(arguments)
        run(
            [
                "cosign",
                "sign-blob",
                "--yes",
                "--tlog-upload=false",
                "--key",
                str(self.work / "cosign.key"),
                "--output-signature",
                str(directory / "release-manifest.sig"),
                str(directory / "release-manifest.json"),
            ],
            env={**self.aws_env, "COSIGN_PASSWORD": ""},
        )
        # A keyless signature carries a certificate; the test key's public key stands in.
        shutil.copy2(self.work / "cosign.pub", directory / "release-manifest.pem")
        for name in (
            "release-bundle.tar.gz",
            "release-manifest.pem",
            "release-manifest.sig",
            "release-manifest.json",
        ):
            self.aws(
                "s3api",
                "put-object",
                "--bucket",
                BUCKET,
                "--key",
                f"{release_id}/{name}",
                "--body",
                str(directory / name),
                "--if-none-match",
                "*",
            )
        # Published releases are immutable.
        overwrite = run(
            [
                "aws",
                "s3api",
                "put-object",
                "--bucket",
                BUCKET,
                "--key",
                f"{release_id}/release-manifest.json",
                "--body",
                str(directory / "release-manifest.json"),
                "--if-none-match",
                "*",
            ],
            env=self.aws_env,
            check=False,
        )
        if overwrite.returncode == 0:
            raise RehearsalError(
                "the releases bucket accepted an overwrite of a published manifest"
            )

    def agent(self, release_id: str | None, *flags: str) -> subprocess.CompletedProcess[str]:
        command = [str(self.work / "agent" / "bin" / "apply-release")]
        if release_id:
            command += ["--release-id", release_id]
        env = {
            **self.aws_env,
            "MOTIONLY_HOST_CONFIG": str(self.host_env),
            "COMPOSE_PROFILES": "slim",
        }
        return run(command + list(flags), env=env, check=False)

    def current(self, link: str = "current") -> str | None:
        path = self.work / "releases" / link
        return path.resolve().name if path.is_symlink() else None

    def check_origin(self) -> dict[str, object]:
        context = ssl.create_default_context(cafile=str(self.ca_cert))
        with urllib.request.urlopen(
            f"{PUBLIC_ORIGIN}/api/health/ready", context=context, timeout=15
        ) as response:
            ready = json.loads(response.read())
        with urllib.request.urlopen(
            f"{PUBLIC_ORIGIN}/login", context=context, timeout=15
        ) as response:
            login = response.status
        if ready.get("environment") != "cpu-stage" or login != 200:
            raise RehearsalError(
                f"the public origin is not serving the release: {ready} /login {login}"
            )
        return {"api_ready": ready.get("status"), "login": login}

    def expect(self, result: subprocess.CompletedProcess[str], code: int, what: str) -> None:
        if result.returncode != code:
            raise RehearsalError(
                f"{what}: expected exit {code}, got {result.returncode}\n"
                f"{result.stdout[-3000:]}\n{result.stderr[-3000:]}"
            )

    # --- the scenario --------------------------------------------------------
    def rehearse(self) -> None:
        first, second, broken = new_release_id(), new_release_id(), new_release_id()
        self.publish(first)
        self.step("published release 1", release=first)
        self.expect(self.agent(first, "--preflight"), 0, "preflight of release 1")
        if run(
            ["docker", "ps", "-q", "--filter", f"label=com.docker.compose.project={PROJECT}"]
        ).stdout.strip():
            raise RehearsalError("preflight started containers")
        self.step("preflight verified release 1 and started nothing")
        self.expect(self.agent(first), 0, "apply of release 1")
        self.step("applied release 1", origin=self.check_origin(), current=self.current())

        self.publish(second)
        self.expect(self.agent(second), 0, "apply of release 2")
        if (self.current(), self.current("previous")) != (second, first):
            raise RehearsalError(
                f"after release 2: current={self.current()} previous={self.current('previous')}"
            )
        self.step("applied release 2", origin=self.check_origin(), current=second, previous=first)

        self.publish(broken, images={**self.images, "api": self.images["gpu-stage-placeholder"]})
        result = self.agent(broken)
        self.expect(result, 70, "apply of the broken release 3")
        if self.current() != second:
            raise RehearsalError(
                f"after the failed release 3, current is {self.current()}, not release 2"
            )
        if "restoring the running release" not in result.stderr:
            raise RehearsalError("the agent did not report restoring the running release")
        self.step(
            "release 3 never became healthy; the agent restored release 2",
            origin=self.check_origin(),
            current=self.current(),
            exit_code=result.returncode,
            agent_output=result.stderr.strip().splitlines()[-6:],
        )

    def save_service_logs(self) -> None:
        """Keep each service's state and recent log for a failed run; teardown
        removes the containers. Logs hold only throwaway rehearsal secrets."""
        saved = RESULTS / "rehearsal-services"
        shutil.rmtree(saved, ignore_errors=True)
        saved.mkdir(parents=True)
        names = run(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                f"label=com.docker.compose.project={PROJECT}",
                "--format",
                "{{.Names}}",
            ],
            check=False,
        ).stdout.split()
        for name in names:
            state = run(
                ["docker", "inspect", "--format", "{{json .State}}", name], check=False
            ).stdout
            logs = run(["docker", "logs", "--tail", "300", name], check=False)
            (saved / f"{name}.log").write_text(
                f"state: {state.strip()}\n\n{logs.stdout}{logs.stderr}", encoding="utf-8"
            )

    def teardown(self) -> None:
        current = self.work / "releases" / "current"
        for release in [current.resolve()] if current.is_symlink() else []:
            run(
                [
                    "docker",
                    "compose",
                    "--project-name",
                    PROJECT,
                    "--profile",
                    "slim",
                    "--env-file",
                    str(release / "compose.env"),
                    "-f",
                    str(release / "bundle" / "infra" / "docker-compose.gpu.yml"),
                    "-f",
                    str(self.overlay),
                    "down",
                    "--volumes",
                    "--remove-orphans",
                    "--timeout",
                    "20",
                ],
                check=False,
            )
        label = f"label=com.docker.compose.project={PROJECT}"
        for kind in ("container", "volume", "network"):
            ids = run(
                ["docker", kind, "ls", "-q", "--filter", label]
                + (["-a"] if kind == "container" else []),
                check=False,
            ).stdout.split()
            if ids:
                run(
                    ["docker", kind, "rm", "-f", *ids]
                    if kind != "network"
                    else ["docker", "network", "rm", *ids],
                    check=False,
                )
        for name in CONTAINERS:
            run(["docker", "rm", "-f", "--volumes", name], check=False)
        # The agent pulled every release image by digest from the local registry.
        # Remove those references only: an ID can be shared with the local
        # CPU-stage tag, which must stay.
        pulled = sorted(
            {
                reference
                for reference in run(
                    [
                        "docker",
                        "image",
                        "ls",
                        "--digests",
                        "--filter",
                        f"reference={REGISTRY}/motionly/*",
                        "--format",
                        "{{.Repository}}@{{.Digest}}",
                    ],
                    check=False,
                ).stdout.split()
                if reference.startswith(f"{REGISTRY}/motionly/") and "@sha256:" in reference
            }
        )
        if pulled:
            run(["docker", "image", "rm", *pulled], check=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(sys.argv[1:] if argv is None else argv)
    if run(
        ["docker", "ps", "-q", "--filter", f"label=com.docker.compose.project={PROJECT}"]
    ).stdout.strip():
        print(
            f"rehearsal: FAILED: Compose project '{PROJECT}' is already running here",
            file=sys.stderr,
        )
        return 1
    RESULTS.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="motionly-rehearsal-"))
    work.chmod(0o700)
    rehearsal = Rehearsal(work)
    status = 1
    started = time.monotonic()
    try:
        rehearsal.start_emulators()
        rehearsal.push_images()
        rehearsal.store_parameters()
        rehearsal.prepare_host()
        rehearsal.rehearse()
        status = 0
    except RehearsalError as error:
        print(f"rehearsal: FAILED: {error}", file=sys.stderr)
        rehearsal.report["error"] = str(error)[-4000:]
        rehearsal.save_service_logs()
    finally:
        rehearsal.teardown()
        shutil.rmtree(work, ignore_errors=True)
        rehearsal.report.update(passed=status == 0, seconds=round(time.monotonic() - started, 1))
        (RESULTS / "rehearsal.json").write_text(
            json.dumps(rehearsal.report, indent=2) + "\n", encoding="utf-8"
        )
    if status == 0:
        log("passed: published, preflighted, applied, upgraded and restored after a failed release")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
