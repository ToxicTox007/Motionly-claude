"""Media subprocesses (ffmpeg, ffprobe, Demucs) never inherit worker secrets."""

import ast
import subprocess
from pathlib import Path

import pytest

from app.services import media_probe
from app.services.subprocess_env import media_tool_env, ml_tool_env

APP = Path(__file__).resolve().parents[1] / "app"

SECRETS = {
    "SUPABASE_SERVICE_ROLE_KEY": "secret-service-role",
    "SUPABASE_JWT_SECRET": "secret-jwt",
    "WORKER_API_TOKEN": "secret-worker-token",
    "REDIS_URL": "redis://:secret-redis@redis:6379/0",
    "GROQ_API_KEY": "secret-groq",
    "LALAL_LICENSE_KEY": "secret-lalal",
    "ADMIN_API_TOKEN": "secret-admin",
}


def test_media_tool_env_is_an_allowlist() -> None:
    environment = media_tool_env(
        {**SECRETS, "PATH": "/usr/bin", "HOME": "/home/motionly", "LC_ALL": "C.UTF-8"}
    )
    assert environment == {"PATH": "/usr/bin", "HOME": "/home/motionly", "LC_ALL": "C.UTF-8"}


def test_ml_tool_env_adds_model_settings_but_no_secrets() -> None:
    environment = ml_tool_env(
        {
            **SECRETS,
            "PATH": "/usr/bin",
            "TORCH_HOME": "/cache/torch",
            "CUDA_VISIBLE_DEVICES": "0",
            "VIRTUAL_ENV": "/opt/venv",
        }
    )
    assert environment == {
        "PATH": "/usr/bin",
        "TORCH_HOME": "/cache/torch",
        "CUDA_VISIBLE_DEVICES": "0",
        "VIRTUAL_ENV": "/opt/venv",
    }


def test_every_media_subprocess_call_passes_a_restricted_env() -> None:
    """A new ffmpeg/ffprobe/Demucs call must not silently inherit os.environ."""
    unrestricted: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"run", "Popen", "check_output", "call"}
                and ast.unparse(node.func.value) == "subprocess"
            ):
                continue
            env = next((kw.value for kw in node.keywords if kw.arg == "env"), None)
            if isinstance(env, ast.Call) and ast.unparse(env.func) in {
                "media_tool_env",
                "ml_tool_env",
            }:
                continue
            if path.name == "queue.py":
                # The BullMQ bridge is not a media tool: it needs REDIS_URL and
                # receives an explicitly built environment.
                assert env is not None
                continue
            unrestricted.append(f"{path.relative_to(APP)}:{node.lineno}")
    assert unrestricted == []


def test_ffprobe_runs_with_the_restricted_env(monkeypatch, tmp_path) -> None:
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)
    seen: list[dict] = []
    real_run = subprocess.run

    def recording_run(command, **kwargs):
        seen.append(kwargs.get("env"))
        return real_run(command, **kwargs)

    monkeypatch.setattr(media_probe.subprocess, "run", recording_run)
    with pytest.raises(media_probe.MediaProbeError):
        media_probe.probe_video(tmp_path / "missing.mp4")

    assert len(seen) == 1 and seen[0] is not None
    assert not set(SECRETS) & set(seen[0])
