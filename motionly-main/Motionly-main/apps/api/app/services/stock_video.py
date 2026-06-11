import hashlib
import json
import time
from dataclasses import dataclass
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

import httpx

from app.api.errors import ApiError
from app.schemas.broll import StockOrientation, StockProviderKey
from app.schemas.plans import BrollRequest, EditPlan

ABSTRACT_TERMS = {
    "growth",
    "success",
    "mindset",
    "value",
    "quality",
    "strategy",
    "thing",
    "idea",
    "important",
    "premium",
    "luxury",
}


@dataclass(frozen=True)
class StockVideoCandidate:
    provider: StockProviderKey
    provider_asset_id: str
    title: str
    thumbnail_url: str | None
    preview_video_url: str | None
    download_url: str | None
    license: str
    width: int
    height: int
    duration_ms: int
    relevance_rank: int
    raw_metadata: dict[str, object]

    @property
    def orientation(self) -> StockOrientation:
        if self.height > self.width:
            return StockOrientation.PORTRAIT
        if self.width > self.height:
            return StockOrientation.LANDSCAPE
        return StockOrientation.SQUARE


class StockVideoProvider(Protocol):
    provider_key: StockProviderKey

    def search(
        self,
        *,
        query: str,
        orientation: StockOrientation,
        min_width: int,
        min_height: int,
    ) -> list[StockVideoCandidate]: ...


@dataclass(frozen=True)
class FixtureStockVideoProvider:
    provider_key: StockProviderKey = StockProviderKey.FIXTURE

    def search(
        self,
        *,
        query: str,
        orientation: StockOrientation,
        min_width: int,
        min_height: int,
    ) -> list[StockVideoCandidate]:
        slug = _slug(query)
        return [
            StockVideoCandidate(
                provider=self.provider_key,
                provider_asset_id=f"fixture-{slug}-{index}",
                title=f"{query.title()} vertical clip {index + 1}",
                thumbnail_url=f"https://assets.motionly.local/broll/{slug}-{index}.jpg",
                preview_video_url=f"fixture://broll/{slug}-{index}",
                download_url=f"fixture://broll/{slug}-{index}/download",
                license="fixture_internal_demo",
                width=1080 if index != 1 else 1920,
                height=1920 if index != 1 else 1080,
                duration_ms=4200 + index * 1600,
                relevance_rank=index + 1,
                raw_metadata={"query": query, "fixture": True},
            )
            for index in range(3)
        ]


@dataclass(frozen=True)
class PexelsStockVideoProvider:
    api_key: str
    provider_key: StockProviderKey = StockProviderKey.PEXELS

    def search(
        self,
        *,
        query: str,
        orientation: StockOrientation,
        min_width: int,
        min_height: int,
    ) -> list[StockVideoCandidate]:
        response = httpx.get(
            "https://api.pexels.com/videos/search",
            headers={"authorization": self.api_key},
            params={
                "query": query,
                "orientation": orientation.value,
                "size": "large",
                "per_page": 10,
            },
            timeout=12,
        )
        if response.status_code >= 400:
            raise ApiError(502, "stock_provider_failed", "Pexels video search failed.")
        videos = response.json().get("videos", [])
        candidates: list[StockVideoCandidate] = []
        for index, item in enumerate(videos):
            files = [
                file
                for file in item.get("video_files", [])
                if int(file.get("width") or 0) >= min_width
                and int(file.get("height") or 0) >= min_height
            ]
            files = files or item.get("video_files", [])
            selected = files[0] if files else {}
            candidates.append(
                StockVideoCandidate(
                    provider=self.provider_key,
                    provider_asset_id=str(item["id"]),
                    title=str(item.get("url") or f"Pexels result {item['id']}"),
                    thumbnail_url=item.get("image"),
                    preview_video_url=selected.get("link"),
                    download_url=selected.get("link"),
                    license="pexels_license",
                    width=int(selected.get("width") or item.get("width") or 0),
                    height=int(selected.get("height") or item.get("height") or 0),
                    duration_ms=int(float(item.get("duration") or 0) * 1000),
                    relevance_rank=index + 1,
                    raw_metadata={"pexels_id": item.get("id"), "url": item.get("url")},
                )
            )
        return candidates


@dataclass(frozen=True)
class PixabayStockVideoProvider:
    api_key: str
    provider_key: StockProviderKey = StockProviderKey.PIXABAY

    def search(
        self,
        *,
        query: str,
        orientation: StockOrientation,
        min_width: int,
        min_height: int,
    ) -> list[StockVideoCandidate]:
        response = httpx.get(
            "https://pixabay.com/api/videos/",
            params={"key": self.api_key, "q": query, "orientation": "vertical", "per_page": 10},
            timeout=12,
        )
        if response.status_code >= 400:
            raise ApiError(502, "stock_provider_failed", "Pixabay video search failed.")
        candidates: list[StockVideoCandidate] = []
        for index, item in enumerate(response.json().get("hits", [])):
            videos = item.get("videos") or {}
            selected = videos.get("large") or videos.get("medium") or videos.get("small") or {}
            picture_id = item.get("picture_id")
            candidates.append(
                StockVideoCandidate(
                    provider=self.provider_key,
                    provider_asset_id=str(item["id"]),
                    title=str(item.get("tags") or f"Pixabay result {item['id']}"),
                    thumbnail_url=(
                        f"https://i.vimeocdn.com/video/{picture_id}_640x360.jpg"
                        if picture_id
                        else None
                    ),
                    preview_video_url=selected.get("url"),
                    download_url=selected.get("url"),
                    license="pixabay_license",
                    width=int(selected.get("width") or 0),
                    height=int(selected.get("height") or 0),
                    duration_ms=int(float(item.get("duration") or 0) * 1000),
                    relevance_rank=index + 1,
                    raw_metadata={"pixabay_id": item.get("id"), "tags": item.get("tags")},
                )
            )
        return candidates


def build_broll_cache_key(
    *,
    edit_plan_id: object,
    density: str,
    schema_version: int,
) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "edit_plan_id": str(edit_plan_id),
                "density": density,
                "schema_version": schema_version,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def keyword_query(request: BrollRequest, style: str) -> str:
    terms: list[str] = []
    for keyword in request.keywords:
        for term in keyword.replace("-", " ").split():
            cleaned = term.strip(".,!?;:()[]").lower()
            if len(cleaned) < 3 or cleaned in ABSTRACT_TERMS or cleaned in terms:
                continue
            terms.append(cleaned)
    if not terms:
        terms = [style, "workspace"]
    return " ".join(terms[:4])


def desired_requests(plan: EditPlan, density: str) -> list[BrollRequest]:
    if density == "from_plan":
        return plan.broll_requests
    limits = {"low": 1, "medium": 2, "high": 4}
    return plan.broll_requests[: limits[density]]


def score_candidate(
    *,
    candidate: StockVideoCandidate,
    slot_duration_ms: int,
    duplicate_provider_asset_ids: set[str],
) -> float:
    portrait_score = 0.32 if candidate.orientation == StockOrientation.PORTRAIT else 0.08
    resolution_score = 0.2 if candidate.width >= 1080 and candidate.height >= 1920 else 0.08
    duration_score = 0.18 if candidate.duration_ms >= slot_duration_ms else 0.08
    rank_score = max(0.0, 0.18 - (candidate.relevance_rank - 1) * 0.035)
    motion_score = 0.08 if candidate.duration_ms >= 3000 else 0.02
    duplicate_penalty = 0.24 if candidate.provider_asset_id in duplicate_provider_asset_ids else 0
    return max(
        0,
        min(
            1,
            portrait_score
            + resolution_score
            + duration_score
            + rank_score
            + motion_score
            - duplicate_penalty,
        ),
    )


def stable_asset_id(provider: StockProviderKey, provider_asset_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"motionly:broll:{provider.value}:{provider_asset_id}"))


def latency_ms_since(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _slug(value: str) -> str:
    slug = "-".join(part for part in value.lower().split() if part)
    return slug[:48] or "clip"
