import json
import os
from pathlib import Path

import httpx
import pytest

from app.core.external_verification import live_provider_contracts_enabled
from app.schemas.broll import StockOrientation
from app.services.stock_video import PexelsStockVideoProvider, PixabayStockVideoProvider

FIXTURES = Path(__file__).parent / "fixtures/providers"


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


def response(name: str, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=fixture(name),
        request=httpx.Request("GET", "https://provider.motionly.test/recorded"),
    )
def test_recorded_stock_provider_contracts(monkeypatch) -> None:
    payloads = iter([response("pexels_video_search.json"), response("pixabay_video_search.json")])
    monkeypatch.setattr("app.services.stock_video.httpx.get", lambda *_a, **_k: next(payloads))
    pexels = PexelsStockVideoProvider(api_key="sandbox").search(
        query="founder", orientation=StockOrientation.PORTRAIT, min_width=720, min_height=1280
    )
    pixabay = PixabayStockVideoProvider(api_key="sandbox").search(
        query="founder", orientation=StockOrientation.PORTRAIT, min_width=720, min_height=1280
    )
    assert (pexels[0].provider_asset_id, pexels[0].duration_ms) == ("12345", 6200)
    assert (pixabay[0].provider_asset_id, pixabay[0].duration_ms) == ("67890", 5000)


LIVE_PROVIDER_CONTRACTS = live_provider_contracts_enabled()

@pytest.mark.skipif(not LIVE_PROVIDER_CONTRACTS, reason="Live provider contracts are disabled")
def test_live_pexels_stock_contract() -> None:
    api_key = os.environ.get("PEXELS_API_KEY")
    if not api_key:
        pytest.skip("PEXELS_API_KEY not set")
    provider = PexelsStockVideoProvider(api_key=api_key)
    result = provider.search(
        query="office",
        orientation=StockOrientation.PORTRAIT,
        min_width=720,
        min_height=1280,
    )
    assert isinstance(result, list)


@pytest.mark.skipif(not LIVE_PROVIDER_CONTRACTS, reason="Live provider contracts are disabled")
def test_live_pexels_stock_bad_credentials() -> None:
    provider = PexelsStockVideoProvider(api_key="bad-key-xyz")
    with pytest.raises(Exception) as excinfo:
        provider.search(
            query="office",
            orientation=StockOrientation.PORTRAIT,
            min_width=720,
            min_height=1280,
        )
    assert "bad-key-xyz" not in str(excinfo.value)


@pytest.mark.skipif(not LIVE_PROVIDER_CONTRACTS, reason="Live provider contracts are disabled")
def test_live_pixabay_stock_contract() -> None:
    api_key = os.environ.get("PIXABAY_API_KEY")
    if not api_key:
        pytest.skip("PIXABAY_API_KEY not set")
    provider = PixabayStockVideoProvider(api_key=api_key)
    result = provider.search(
        query="office",
        orientation=StockOrientation.PORTRAIT,
        min_width=720,
        min_height=1280,
    )
    assert isinstance(result, list)


@pytest.mark.skipif(not LIVE_PROVIDER_CONTRACTS, reason="Live provider contracts are disabled")
def test_live_pixabay_stock_bad_credentials() -> None:
    provider = PixabayStockVideoProvider(api_key="bad-key-xyz")
    with pytest.raises(Exception) as excinfo:
        provider.search(
            query="office",
            orientation=StockOrientation.PORTRAIT,
            min_width=720,
            min_height=1280,
        )
    assert "bad-key-xyz" not in str(excinfo.value)
