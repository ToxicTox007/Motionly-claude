"""Deployment retirement guard for the static Compose GPU topology."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

# Construct the retired identifiers so this guard can scan test files too without
# exempting its own source from the deployment-surface audit.
RETIRED_IDENTIFIERS = (
    "azure" + "_aks" + "_keda",
    "Azure" + "GpuPoolBackend",
    "azure" + "_gpu",
    "AZURE" + "_GPU",
    "AK" + "S",
    "KE" + "DA",
)
AUDITED_DIRECTORIES = (
    ROOT / "apps/api/app",
    ROOT / "apps/api/tests",
    ROOT / "infra",
    ROOT / "scripts",
    ROOT / "docs",
    ROOT / ".github",
)
TEXT_SUFFIXES = {".md", ".mjs", ".py", ".sh", ".ts", ".tsx", ".yaml", ".yml"}


def test_static_compose_retirement_removes_managed_provider_surfaces() -> None:
    assert not (ROOT / "infra" / "azure").exists()
    assert not (ROOT / "apps/api/app/services" / ("azure" + "_gpu.py")).exists()

    offenders: list[str] = []
    for directory in AUDITED_DIRECTORIES:
        for path in directory.rglob("*"):
            if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
                continue
            source = path.read_text(encoding="utf-8")
            for identifier in RETIRED_IDENTIFIERS:
                if identifier in source:
                    offenders.append(f"{path.relative_to(ROOT)}: {identifier}")
    assert not offenders, (
        "retired managed GPU deployment identifiers remain: " + ", ".join(offenders)
    )


def test_azure_openai_model_provider_remains_supported() -> None:
    adapter_source = (ROOT / "apps/api/app/services/model_adapters.py").read_text(encoding="utf-8")
    gateway_source = (ROOT / "apps/api/app/services/model_gateway_factory.py").read_text(
        encoding="utf-8"
    )
    config_source = (ROOT / "apps/api/app/core/config.py").read_text(encoding="utf-8")

    assert "AzureOpenAIAdapter" in adapter_source
    assert 'adapters["azure"] = AzureOpenAIAdapter' in gateway_source
    assert "azure_openai_endpoint" in config_source
