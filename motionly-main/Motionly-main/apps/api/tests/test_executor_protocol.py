"""Persistent executors keep stdout for protocol replies only.

Model libraries print to stdout (sometimes JSON-looking lines, sometimes from
C code writing to file descriptor 1). The worker treats a non-reply line that
starts with ``{`` or ``[`` as a protocol violation and kills the executor, so
executors must move all such output to stderr.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import textwrap
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]

NOISY_EXECUTOR = textwrap.dedent(
    """
    import os, subprocess, sys
    from app.workers.executor_protocol import open_protocol_channel

    channel = open_protocol_channel()
    for line in sys.stdin:
        # What a model library might do while a job runs:
        print('{"progress": 0.5}')                     # Python-level print
        sys.stdout.write("[warn] noisy\\n")             # sys.stdout writes
        os.write(1, b'{"native": "fd 1 write"}\\n')    # C-level write to fd 1
        subprocess.run([sys.executable, "-c", "print('[child] inherited fd 1')"])
        channel.send({"id": 7, "result": {"ok": True}})
    """
)


def _run(script: str, stdin: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", script],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=API_ROOT,
        timeout=60,
        check=True,
    )


def test_only_protocol_replies_reach_stdout() -> None:
    result = _run(NOISY_EXECUTOR, '{"id": 7, "payload": {}}\n')

    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert [json.loads(line) for line in lines] == [{"id": 7, "result": {"ok": True}}]
    for noise in ('{"progress": 0.5}', "[warn] noisy", '{"native": "fd 1 write"}', "[child]"):
        assert noise in result.stderr


def test_control_without_the_channel_corrupts_stdout() -> None:
    """Proves the probe: the same noise without the channel lands on stdout."""
    script = NOISY_EXECUTOR.replace(
        "channel = open_protocol_channel()",
        "import json\n"
        "class _Plain:\n"
        "    def send(self, m): print(json.dumps(m), flush=True)\n"
        "channel = _Plain()",
    )
    result = _run(script, '{"id": 7, "payload": {}}\n')
    assert '{"progress": 0.5}' in result.stdout
    assert '{"native": "fd 1 write"}' in result.stdout


def test_every_executor_replies_only_through_the_channel() -> None:
    """domain/visual/matte --serve loops open the channel first and never print()."""
    for name in ("domain_job.py", "visual_job.py", "matte_job.py"):
        tree = ast.parse((API_ROOT / "app" / "workers" / name).read_text(encoding="utf-8"))
        serve = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_serve"
        )
        first = serve.body[0]
        assert "open_protocol_channel" in ast.unparse(first), name
        prints_to_stdout = [
            node.lineno
            for node in ast.walk(serve)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "print"
            and not any(kw.arg == "file" for kw in node.keywords)
        ]
        assert prints_to_stdout == [], (name, prints_to_stdout)
