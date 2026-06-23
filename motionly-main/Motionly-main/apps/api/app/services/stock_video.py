import hashlib
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import quote
from uuid import NAMESPACE_URL, uuid5

import httpx

from app.api.errors import ApiError
from app.schemas.broll import StockOrientation, StockProviderKey
from app.schemas.plans import BrollRequest, EditPlan
from app.services.storage import StorageSigner


def ensure_owned_stock_asset(
    *, asset, repository, settings, storage_signer: StorageSigner
):
    if asset.provider not in {StockProviderKey.PEXELS, StockProviderKey.PIXABAY}:
        return asset
    if asset.raw_metadata.get("owned_copy"):
        return asset
    source_url = asset.download_url or asset.preview_video_url
    if not source_url or not source_url.startswith(("http://", "https://")):
        raise ApiError(502, "stock_asset_source_missing", "Selected stock video is unavailable.")
    bucket = settings.upload_storage_bucket
    object_id = hashlib.sha256(
        f"{asset.provider.value}:{asset.provider_asset_id}".encode()
    ).hexdigest()[:24]
    path = (
        f"workspaces/{asset.workspace_id}/projects/{asset.project_id}/"
        f"broll/stock/{asset.provider.value}-{object_id}.mp4"
    )
    try:
        with httpx.stream("GET", source_url, timeout=180, follow_redirects=True) as source:
            source.raise_for_status()
            content_type = source.headers.get("content-type", "video/mp4")
            if storage_signer.supabase_url and storage_signer.service_role_key:
                encoded_path = quote(path, safe="/")
                uploaded = httpx.post(
                    f"{storage_signer.supabase_url}/storage/v1/object/{bucket}/{encoded_path}",
                    headers={
                        "apikey": storage_signer.service_role_key,
                        "authorization": f"Bearer {storage_signer.service_role_key}",
                        "content-type": content_type,
                        "x-upsert": "true",
                    },
                    content=source.iter_bytes(chunk_size=1024 * 1024),
                    timeout=300,
                )
                if uploaded.status_code >= 400:
                    raise ApiError(502, "stock_asset_ingest_failed", "Stock video copy failed.")
            else:
                target = Path(settings.media_work_dir).expanduser().resolve() / bucket / path
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("wb") as output:
                    for chunk in source.iter_bytes(chunk_size=1024 * 1024):
                        output.write(chunk)
    except httpx.HTTPError as exc:
        raise ApiError(502, "stock_asset_ingest_failed", "Stock video copy failed.") from exc
    signed = storage_signer.create_signed_download(
        bucket=bucket,
        path=path,
        expires_in_seconds=StorageSigner.MAX_DOWNLOAD_TTL_SECONDS,
    )
    return repository.mark_stock_asset_owned(
        asset=asset,
        bucket=bucket,
        path=path,
        download_url=signed.url,
    )

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
                raw_metadata={
                    "query": query,
                    "fixture": True,
                    "shot_type": _fixture_shot_type(query, index),
                    "embedding": _hash_embedding(
                        f"{query}:{_fixture_shot_type(query, index)}:{index}",
                        dimensions=32,
                    ),
                },
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
    semantic_context: object | None = None,
) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "edit_plan_id": str(edit_plan_id),
                "density": density,
                "schema_version": schema_version,
                "semantic_context": semantic_context,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def keyword_query(request: BrollRequest, style: str) -> str:
    if request.semantic_query:
        return request.semantic_query
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
    semantic_query: str | None = None,
    semantic_embedding: list[float] | None = None,
    target_shot_type: str | None = None,
    used_shot_types: set[str] | None = None,
) -> float:
    shot_type = candidate_shot_type(candidate)
    portrait_score = 0.24 if candidate.orientation == StockOrientation.PORTRAIT else 0.06
    resolution_score = 0.18 if candidate.width >= 1080 and candidate.height >= 1920 else 0.06
    duration_score = 0.14 if candidate.duration_ms >= slot_duration_ms else 0.06
    rank_score = max(0.0, 0.13 - (candidate.relevance_rank - 1) * 0.028)
    motion_score = 0.05 if candidate.duration_ms >= 3000 else 0.015
    semantic_score = 0.18 * semantic_match_score(
        candidate=candidate,
        semantic_query=semantic_query,
        semantic_embedding=semantic_embedding,
    )
    shot_match_score = 0.08 if target_shot_type and shot_type == target_shot_type else 0.02
    diversity_bonus = 0.04 if used_shot_types and shot_type not in used_shot_types else 0
    diversity_penalty = 0.08 if used_shot_types and shot_type in used_shot_types else 0
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
            + semantic_score
            + shot_match_score
            + diversity_bonus
            - diversity_penalty
            - duplicate_penalty,
        ),
    )


def semantic_match_score(
    *,
    candidate: StockVideoCandidate,
    semantic_query: str | None = None,
    semantic_embedding: list[float] | None = None,
) -> float:
    embedding_score = 0.0
    raw_embedding = candidate.raw_metadata.get("embedding")
    if semantic_embedding and isinstance(raw_embedding, list):
        candidate_embedding = [
            float(value) for value in raw_embedding if isinstance(value, (int, float))
        ]
        embedding_score = _cosine_similarity(semantic_embedding, candidate_embedding)
    lexical_score = _lexical_similarity(
        semantic_query or "",
        " ".join(
            str(value)
            for value in (
                candidate.title,
                candidate.raw_metadata.get("query"),
                candidate.raw_metadata.get("tags"),
            )
            if value
        ),
    )
    return max(embedding_score, lexical_score)


def candidate_shot_type(candidate: StockVideoCandidate) -> str:
    raw = candidate.raw_metadata.get("shot_type")
    if isinstance(raw, str) and raw.strip():
        return raw.strip().lower()
    return _infer_shot_type(" ".join([candidate.title, str(candidate.raw_metadata)]))


def stable_asset_id(provider: StockProviderKey, provider_asset_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"motionly:broll:{provider.value}:{provider_asset_id}"))


def latency_ms_since(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _slug(value: str) -> str:
    slug = "-".join(part for part in value.lower().split() if part)
    return slug[:48] or "clip"


def _fixture_shot_type(query: str, index: int) -> str:
    primary = _infer_shot_type(query)
    alternates = [
        primary,
        "establishing",
        "product_detail" if primary != "product_detail" else "context_cutaway",
    ]
    return alternates[index % len(alternates)]


def _infer_shot_type(text: str) -> str:
    lowered = text.lower()
    if any(term in lowered for term in ("macro", "detail", "product", "watch", "shoe", "bag")):
        return "product_detail"
    if any(term in lowered for term in ("city", "hotel", "landscape", "exterior", "travel")):
        return "establishing"
    if any(term in lowered for term in ("screen", "tutorial", "demo", "laptop", "software")):
        return "screen_insert"
    if any(term in lowered for term in ("text", "title", "graphic", "chart", "diagram")):
        return "graphic_insert"
    if any(term in lowered for term in ("people", "crowd", "audience", "team")):
        return "people_context"
    if any(term in lowered for term in ("office", "workspace", "founder", "creator")):
        return "context_cutaway"
    return "cutaway"


def _hash_embedding(seed: str, *, dimensions: int) -> list[float]:
    values = []
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    while len(values) < dimensions:
        for byte in digest:
            values.append(round((byte / 255) * 2 - 1, 6))
            if len(values) == dimensions:
                break
        digest = hashlib.sha256(digest).digest()
    norm = math.sqrt(sum(value * value for value in values)) or 1
    return [round(value / norm, 6) for value in values]


def _cosine_similarity(first: list[float], second: list[float]) -> float:
    size = min(len(first), len(second))
    if size == 0:
        return 0.0
    left = [float(value) for value in first[:size]]
    right = [float(value) for value in second[:size]]
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    cosine = sum(a * b for a, b in zip(left, right, strict=False)) / (left_norm * right_norm)
    return max(0.0, min(1.0, (cosine + 1) / 2))


def _lexical_similarity(query: str, candidate_text: str) -> float:
    query_terms = _terms(query)
    candidate_terms = _terms(candidate_text)
    if not query_terms or not candidate_terms:
        return 0.0
    overlap = len(query_terms & candidate_terms)
    return min(1.0, overlap / max(1, len(query_terms)))


def _terms(text: str) -> set[str]:
    return {
        term.strip(".,!?;:()[]").lower()
        for term in text.replace("-", " ").split()
        if len(term.strip(".,!?;:()[]")) >= 3
        and term.strip(".,!?;:()[]").lower() not in ABSTRACT_TERMS
    }
