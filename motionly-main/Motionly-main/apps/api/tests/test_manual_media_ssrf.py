from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_remotion_worker_clears_ambient_proxy_egress() -> None:
    compose = (ROOT / "infra" / "docker-compose.gpu.yml").read_text(encoding="utf-8")
    start = compose.index("  remotion-worker:\n")
    end = compose.index("\n  publishing-worker:\n", start)
    remotion = compose[start:end]

    assert 'HTTP_PROXY: ""' in remotion
    assert 'HTTPS_PROXY: ""' in remotion
    assert 'ALL_PROXY: ""' in remotion
    assert "NO_PROXY: 127.0.0.1,localhost,::1" in remotion
    assert "loopback URLs from the worker media proxy" in remotion
