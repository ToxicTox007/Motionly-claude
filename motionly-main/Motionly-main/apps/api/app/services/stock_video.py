import hashlib
import json
import logging
import math
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Protocol
from urllib.parse import quote
from uuid import NAMESPACE_URL, uuid5

import httpx

from app.api.errors import ApiError
from app.schemas.broll import (
    BrollConfidenceLabel,
    StockAssetEnrichmentStatus,
    StockOrientation,
    StockProviderKey,
)
from app.schemas.plans import BrollRequest, EditPlan
from app.services.media_url_policy import MediaUrlPolicyError, safe_http_stream
from app.services.storage import StorageSigner

EMBEDDING_DOWNLOAD_TIMEOUT_SECONDS = 6.0
EMBEDDING_DOWNLOAD_MAX_ATTEMPTS = 2
EMBEDDING_DOWNLOAD_MAX_BYTES = 8 * 1024 * 1024
# The workspace copy of a stock clip is fetched again at render time, where
# media downloads are capped at 512 MiB (worker_media.MAX_SOURCE_BYTES), so a
# larger clip could never render anyway. STOCK_ASSET_MAX_BYTES can lower it.
DEFAULT_STOCK_ASSET_MAX_BYTES = 512 * 1024 * 1024


class StockAssetTooLarge(Exception):
    """A provider asset exceeded the stock-ingest byte cap."""


def stock_asset_max_bytes() -> int:
    try:
        value = int(os.getenv("STOCK_ASSET_MAX_BYTES", str(DEFAULT_STOCK_ASSET_MAX_BYTES)))
    except ValueError:
        return DEFAULT_STOCK_ASSET_MAX_BYTES
    return max(1, min(DEFAULT_STOCK_ASSET_MAX_BYTES, value))


def _reject_declared_oversize(content_length: str | None, max_bytes: int) -> None:
    try:
        declared = int(content_length) if content_length is not None else None
    except ValueError:
        declared = None
    if declared is not None and declared > max_bytes:
        raise StockAssetTooLarge(f"declared {declared} bytes > {max_bytes}")


def _bounded_chunks(chunks, max_bytes: int):
    """Yield chunks, stopping the copy once more than ``max_bytes`` arrive.

    Content-Length is optional (and can understate the body), so the bytes
    actually streamed are what count.
    """
    received = 0
    for chunk in chunks:
        received += len(chunk)
        if received > max_bytes:
            raise StockAssetTooLarge(f"streamed more than {max_bytes} bytes")
        yield chunk


_ALLOWED_THUMBNAIL_CONTENT_TYPES = {"image/gif", "image/jpeg", "image/png", "image/webp"}
COMPATIBLE_CLIP_MODEL_VERSION = "compatible-hash-clip-v1"
logger = logging.getLogger(__name__)


def ensure_owned_stock_asset(*, asset, repository, settings, storage_signer: StorageSigner):
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
        with safe_http_stream(
            source_url,
            timeout=180,
        ) as source:
            source.raise_for_status()
            content_type = source.headers.get("content-type", "video/mp4")
            max_bytes = stock_asset_max_bytes()
            _reject_declared_oversize(source.headers.get("content-length"), max_bytes)
            chunks = _bounded_chunks(source.iter_bytes(chunk_size=1024 * 1024), max_bytes)
            if storage_signer.supabase_url and storage_signer.service_role_key:
                encoded_path = quote(path, safe="/")
                object_url = (
                    f"{storage_signer.supabase_url}/storage/v1/object/{bucket}/{encoded_path}"
                )
                storage_headers = {
                    "apikey": storage_signer.service_role_key,
                    "authorization": f"Bearer {storage_signer.service_role_key}",
                }
                try:
                    uploaded = httpx.post(
                        object_url,
                        headers={
                            **storage_headers,
                            "content-type": content_type,
                            "x-upsert": "true",
                        },
                        content=chunks,
                        timeout=300,
                    )
                except StockAssetTooLarge:
                    # The upload was cut off mid-stream; make sure no partial
                    # object is left behind (best effort).
                    try:
                        httpx.delete(object_url, headers=storage_headers, timeout=30)
                    except httpx.HTTPError:
                        logger.warning("stock_asset_partial_cleanup_failed")
                    raise
                if uploaded.status_code >= 400:
                    raise ApiError(502, "stock_asset_ingest_failed", "Stock video copy failed.")
            else:
                target = Path(settings.media_work_dir).expanduser().resolve() / bucket / path
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with target.open("wb") as output:
                        for chunk in chunks:
                            output.write(chunk)
                except BaseException:
                    target.unlink(missing_ok=True)
                    raise
    except StockAssetTooLarge as exc:
        raise ApiError(
            422,
            "stock_asset_too_large",
            "This stock video is too large to use. Pick another clip.",
        ) from exc
    except (httpx.HTTPError, MediaUrlPolicyError) as exc:
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
    provider_url: str | None = None
    source_url: str | None = None
    license_metadata: dict[str, object] | None = None
    attribution_metadata: dict[str, object] | None = None

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
                provider_url=f"https://assets.motionly.local/providers/fixture/{slug}-{index}",
                source_url=f"https://assets.motionly.local/broll/{slug}-{index}.mp4",
                license_metadata={"license": "fixture_internal_demo"},
                attribution_metadata={"provider": "fixture"},
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
                    provider_url=str(item.get("url") or ""),
                    source_url=selected.get("link"),
                    license_metadata={"license": "pexels_license"},
                    attribution_metadata={"user": item.get("user"), "url": item.get("url")},
                    raw_metadata={
                        "pexels_id": item.get("id"),
                        "url": item.get("url"),
                        "video_pictures": item.get("video_pictures"),
                    },
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
                    provider_url=(
                        f"https://pixabay.com/videos/id-{item['id']}/" if item.get("id") else None
                    ),
                    source_url=selected.get("url"),
                    license_metadata={"license": "pixabay_license"},
                    attribution_metadata={"user": item.get("user"), "tags": item.get("tags")},
                    raw_metadata={
                        "pixabay_id": item.get("id"),
                        "tags": item.get("tags"),
                        "page_url": item.get("pageURL"),
                    },
                )
            )
        return candidates


@dataclass(frozen=True)
class CandidateScoreDetails:
    total: float
    confidence_label: BrollConfidenceLabel
    semantic_score: float
    embedding_score: float
    lexical_score: float
    orientation_score: float
    duration_score: float
    diversity_score: float
    provider_license_score: float
    shot_type_score: float
    reasons: list[str]


class CompatibleClipEmbeddingProvider:
    def __init__(
        self,
        *,
        model_name: str = "openai/clip-vit-base-patch32",
        allow_fallback: bool = True,
        revision: str | None = None,
    ) -> None:
        self.model_name = model_name
        self.allow_fallback = allow_fallback
        self.revision = revision
        self._loaded: dict[str, object] | None = None
        self._fallback = HashClipEmbeddingProvider(model_name=COMPATIBLE_CLIP_MODEL_VERSION)

    @property
    def model_version(self) -> str:
        if self._loaded is None and self.allow_fallback:
            return self._fallback.model_version
        return self.model_name

    def embed_text(self, text: str) -> list[float]:
        if not text.strip():
            return []
        try:
            models = self._load()
        except Exception:
            if self.allow_fallback:
                return self._fallback.embed_text(text)
            raise
        torch = models["torch"]
        device = models["device"]
        processor = models["processor"]
        model = models["model"]
        inputs = processor(text=[text], return_tensors="pt", padding=True).to(device)
        with torch.no_grad():
            embedding = model.get_text_features(**inputs)
        embedding = embedding / embedding.norm(dim=-1, keepdim=True)
        return [round(float(value), 6) for value in embedding[0].detach().cpu().tolist()]

    def embed_image(self, *, image_bytes: bytes, source_hint: str | None = None) -> list[float]:
        if not image_bytes and not source_hint:
            return []
        try:
            models = self._load()
        except Exception:
            if self.allow_fallback:
                return self._fallback.embed_image(
                    image_bytes=image_bytes,
                    source_hint=source_hint,
                )
            raise
        Image = models["Image"]
        torch = models["torch"]
        device = models["device"]
        processor = models["processor"]
        model = models["model"]
        image = Image.open(BytesIO(image_bytes)).convert("RGB")
        inputs = processor(images=image, return_tensors="pt").to(device)
        with torch.no_grad():
            embedding = model.get_image_features(**inputs)
        embedding = embedding / embedding.norm(dim=-1, keepdim=True)
        return [round(float(value), 6) for value in embedding[0].detach().cpu().tolist()]

    def _load(self) -> dict[str, object]:
        if self._loaded is not None:
            return self._loaded
        import torch
        from PIL import Image
        from transformers import CLIPModel, CLIPProcessor

        device = "cuda" if torch.cuda.is_available() else "cpu"
        processor = CLIPProcessor.from_pretrained(
            self.model_name,
            revision=self.revision,
            local_files_only=True,
        )
        model = CLIPModel.from_pretrained(
            self.model_name,
            revision=self.revision,
            local_files_only=True,
        ).to(device)
        self._loaded = {
            "Image": Image,
            "torch": torch,
            "device": device,
            "processor": processor,
            "model": model,
        }
        return self._loaded


class HashClipEmbeddingProvider:
    def __init__(self, *, model_name: str = COMPATIBLE_CLIP_MODEL_VERSION) -> None:
        self.model_version = model_name

    def embed_text(self, text: str) -> list[float]:
        return _hash_embedding(text, dimensions=32)

    def embed_image(self, *, image_bytes: bytes, source_hint: str | None = None) -> list[float]:
        seed = image_bytes.hex()[:256] if image_bytes else (source_hint or "stock-asset")
        return _hash_embedding(seed, dimensions=32)


def stock_embedding_provider(*, model_name: str = "openai/clip-vit-base-patch32"):
    return CompatibleClipEmbeddingProvider(model_name=model_name)


def build_stock_asset_embedding_cache_key(
    *,
    provider: StockProviderKey,
    provider_asset_id: str,
    embedding_model_version: str,
) -> str:
    return hashlib.sha256(
        f"{provider.value}:{provider_asset_id}:{embedding_model_version}".encode()
    ).hexdigest()


def build_stock_asset_raw_metadata(
    *,
    candidate: StockVideoCandidate,
    query: str,
    semantic_query: str,
    target_shot_type: str | None,
) -> dict[str, object]:
    metadata = {
        **candidate.raw_metadata,
        "query": query,
        "semantic_query": semantic_query,
        "target_shot_type": target_shot_type,
        "provider_url": candidate.provider_url,
        "source_url": candidate.source_url or candidate.download_url or candidate.preview_video_url,
        "thumbnail_url": candidate.thumbnail_url,
        "license_metadata": candidate.license_metadata or {"license": candidate.license},
        "attribution_metadata": candidate.attribution_metadata or {},
        "orientation": candidate.orientation.value,
        "duration_ms": candidate.duration_ms,
        "dimensions": {"width": candidate.width, "height": candidate.height},
        "enrichment_status": (
            StockAssetEnrichmentStatus.PENDING.value
            if candidate.provider in {StockProviderKey.PEXELS, StockProviderKey.PIXABAY}
            else StockAssetEnrichmentStatus.SKIPPED.value
        ),
        "enrichment_failure_reason": None,
    }
    if "embedding" in candidate.raw_metadata:
        metadata["embedding"] = candidate.raw_metadata["embedding"]
        metadata["embedding_model_version"] = candidate.raw_metadata.get(
            "embedding_model_version", COMPATIBLE_CLIP_MODEL_VERSION
        )
        metadata["enrichment_status"] = StockAssetEnrichmentStatus.COMPLETED.value
    return metadata


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


def stock_asset_enrichment_refresh_key(asset) -> str:
    """Return a stable source fingerprint for duplicate-safe enrichment dispatch."""
    metadata = asset.raw_metadata
    source_identity = {
        "provider": asset.provider.value,
        "provider_asset_id": asset.provider_asset_id,
        "thumbnail_url": metadata.get("thumbnail_url") or asset.thumbnail_url,
        "source_url": metadata.get("source_url") or asset.download_url or asset.preview_video_url,
        "provider_url": metadata.get("provider_url"),
        "query": metadata.get("query"),
        "semantic_query": metadata.get("semantic_query"),
        "target_shot_type": metadata.get("target_shot_type"),
        "license_metadata": metadata.get("license_metadata"),
        "attribution_metadata": metadata.get("attribution_metadata"),
    }
    serialized = json.dumps(
        source_identity,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def enqueue_stock_asset_enrichment(*, queue, asset, clip_model_name: str):
    """Enqueue the persisted asset for asynchronous, idempotent enrichment."""
    return queue.enqueue_asset_enrichment(
        project_id=asset.project_id,
        workspace_id=asset.workspace_id,
        asset_id=asset.id,
        refresh_key=stock_asset_enrichment_refresh_key(asset),
        job_payload=json.dumps(
            {"clip_model_name": clip_model_name},
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


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
    adjacent_embeddings: list[list[float]] | None = None,
) -> float:
    return score_candidate_details(
        candidate=candidate,
        slot_duration_ms=slot_duration_ms,
        duplicate_provider_asset_ids=duplicate_provider_asset_ids,
        semantic_query=semantic_query,
        semantic_embedding=semantic_embedding,
        target_shot_type=target_shot_type,
        used_shot_types=used_shot_types,
        adjacent_embeddings=adjacent_embeddings,
    ).total


def score_candidate_details(
    *,
    candidate: StockVideoCandidate,
    slot_duration_ms: int,
    duplicate_provider_asset_ids: set[str],
    semantic_query: str | None = None,
    semantic_embedding: list[float] | None = None,
    target_shot_type: str | None = None,
    used_shot_types: set[str] | None = None,
    adjacent_embeddings: list[list[float]] | None = None,
) -> CandidateScoreDetails:
    shot_type = candidate_shot_type(candidate)
    embedding_score, lexical_score, reasons = semantic_match_breakdown(
        candidate=candidate,
        semantic_query=semantic_query,
        semantic_embedding=semantic_embedding,
    )
    embedding_available = embedding_score > 0
    semantic_score = embedding_score if embedding_available else lexical_score * 0.45
    lexical_tiebreaker = lexical_score if embedding_available else lexical_score * 0.1
    orientation_score = 1.0 if candidate.orientation == StockOrientation.PORTRAIT else 0.35
    duration_delta = abs(candidate.duration_ms - slot_duration_ms)
    duration_score = max(0.0, min(1.0, 1 - (duration_delta / max(slot_duration_ms, 1))))
    shot_type_score = 1.0 if target_shot_type and shot_type == target_shot_type else 0.35
    diversity_score = diversity_signal(
        candidate=candidate,
        used_shot_types=used_shot_types,
        adjacent_embeddings=adjacent_embeddings,
    )
    provider_license_score = provider_license_signal(candidate)
    relevance_rank_score = max(0.0, 1 - ((candidate.relevance_rank - 1) * 0.14))
    duplicate_penalty = 0.45 if candidate.provider_asset_id in duplicate_provider_asset_ids else 0.0
    embedding_penalty = 0.18 if semantic_embedding and not embedding_available else 0.0
    total = max(
        0.0,
        min(
            1.0,
            (semantic_score * 0.4)
            + (orientation_score * 0.18)
            + (duration_score * 0.14)
            + (lexical_tiebreaker * 0.11)
            + (diversity_score * 0.09)
            + (provider_license_score * 0.05)
            + (shot_type_score * 0.02)
            + (relevance_rank_score * 0.01)
            - embedding_penalty
            - duplicate_penalty,
        ),
    )
    confidence_label = _candidate_confidence_label(
        total=total,
        embedding_score=embedding_score,
        lexical_score=lexical_score,
        candidate=candidate,
    )
    return CandidateScoreDetails(
        total=total,
        confidence_label=confidence_label,
        semantic_score=semantic_score,
        embedding_score=embedding_score,
        lexical_score=lexical_score,
        orientation_score=orientation_score,
        duration_score=duration_score,
        diversity_score=diversity_score,
        provider_license_score=provider_license_score,
        shot_type_score=shot_type_score,
        reasons=reasons,
    )


def semantic_match_score(
    *,
    candidate: StockVideoCandidate,
    semantic_query: str | None = None,
    semantic_embedding: list[float] | None = None,
) -> float:
    embedding_score, lexical_score, _reasons = semantic_match_breakdown(
        candidate=candidate,
        semantic_query=semantic_query,
        semantic_embedding=semantic_embedding,
    )
    return max(embedding_score, lexical_score)


def semantic_match_breakdown(
    *,
    candidate: StockVideoCandidate,
    semantic_query: str | None = None,
    semantic_embedding: list[float] | None = None,
) -> tuple[float, float, list[str]]:
    reasons: list[str] = []
    embedding_score = 0.0
    raw_embedding = candidate.raw_metadata.get("embedding")
    candidate_embedding = (
        [float(value) for value in raw_embedding if isinstance(value, int | float)]
        if isinstance(raw_embedding, list)
        else []
    )
    if semantic_embedding and candidate_embedding:
        if len(semantic_embedding) != len(candidate_embedding):
            reasons.append("embedding_dimension_mismatch")
        else:
            embedding_score = _cosine_similarity(semantic_embedding, candidate_embedding)
    elif semantic_embedding and not candidate_embedding:
        reasons.append(
            "enrichment_failed"
            if candidate.raw_metadata.get("enrichment_status")
            == StockAssetEnrichmentStatus.FAILED.value
            else "candidate_embedding_missing"
        )
    lexical_score = _lexical_similarity(
        semantic_query or "",
        " ".join(
            str(value)
            for value in (
                candidate.title,
                candidate.raw_metadata.get("query"),
                candidate.raw_metadata.get("tags"),
                candidate.raw_metadata.get("semantic_query"),
            )
            if value
        ),
    )
    if embedding_score == 0 and lexical_score > 0:
        reasons.append("lexical_fallback")
    return embedding_score, lexical_score, reasons


def provider_license_signal(candidate: StockVideoCandidate) -> float:
    base = 0.8 if candidate.provider in {StockProviderKey.PEXELS, StockProviderKey.PIXABAY} else 0.6
    if candidate.license and "license" in candidate.license.lower():
        base += 0.1
    if candidate.source_url or candidate.provider_url:
        base += 0.1
    return max(0.0, min(1.0, base))


def diversity_signal(
    *,
    candidate: StockVideoCandidate,
    used_shot_types: set[str] | None,
    adjacent_embeddings: list[list[float]] | None,
) -> float:
    shot_type = candidate_shot_type(candidate)
    if adjacent_embeddings and isinstance(candidate.raw_metadata.get("embedding"), list):
        candidate_embedding = [
            float(value)
            for value in candidate.raw_metadata["embedding"]
            if isinstance(value, int | float)
        ]
        similarities = [
            _cosine_similarity(candidate_embedding, embedding)
            for embedding in adjacent_embeddings
            if embedding
        ]
        if similarities:
            return max(0.0, 1 - max(similarities))
    if not used_shot_types:
        return 0.7
    return 0.9 if shot_type not in used_shot_types else 0.2


def _candidate_confidence_label(
    *,
    total: float,
    embedding_score: float,
    lexical_score: float,
    candidate: StockVideoCandidate,
) -> BrollConfidenceLabel:
    status = candidate.raw_metadata.get("enrichment_status")
    if embedding_score <= 0 and status == StockAssetEnrichmentStatus.FAILED.value:
        return BrollConfidenceLabel.ENRICHMENT_FAILED
    if embedding_score <= 0 and lexical_score > 0:
        return BrollConfidenceLabel.LEXICAL_FALLBACK
    if total >= 0.78:
        return BrollConfidenceLabel.HIGH
    if total >= 0.58:
        return BrollConfidenceLabel.MEDIUM
    return BrollConfidenceLabel.LOW


def candidate_shot_type(candidate: StockVideoCandidate) -> str:
    raw = candidate.raw_metadata.get("shot_type")
    if isinstance(raw, str) and raw.strip():
        return raw.strip().lower()
    return _infer_shot_type(" ".join([candidate.title, str(candidate.raw_metadata)]))


def stable_asset_id(provider: StockProviderKey, provider_asset_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"motionly:broll:{provider.value}:{provider_asset_id}"))


def latency_ms_since(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def ensure_candidate_semantic_embedding(
    *,
    candidate: StockVideoCandidate,
    semantic_query: str,
    clip_model_name: str = "openai/clip-vit-base-patch32",
    embedding_provider=None,
    workspace_id: object | None = None,
    project_id: object | None = None,
) -> StockVideoCandidate:
    if isinstance(candidate.raw_metadata.get("embedding"), list):
        return candidate
    provider = embedding_provider or stock_embedding_provider(model_name=clip_model_name)
    metadata = {
        **candidate.raw_metadata,
        "embedding": _provider_embed_text(
            provider,
            semantic_query,
            workspace_id=workspace_id,
            project_id=project_id,
        ),
        "embedding_model_version": provider.model_version,
        "enrichment_status": StockAssetEnrichmentStatus.COMPLETED.value,
    }
    return StockVideoCandidate(
        provider=candidate.provider,
        provider_asset_id=candidate.provider_asset_id,
        title=candidate.title,
        thumbnail_url=candidate.thumbnail_url,
        preview_video_url=candidate.preview_video_url,
        download_url=candidate.download_url,
        license=candidate.license,
        width=candidate.width,
        height=candidate.height,
        duration_ms=candidate.duration_ms,
        relevance_rank=candidate.relevance_rank,
        provider_url=candidate.provider_url,
        source_url=candidate.source_url,
        license_metadata=candidate.license_metadata,
        attribution_metadata=candidate.attribution_metadata,
        raw_metadata=metadata,
    )


def _read_bounded_thumbnail(response: httpx.Response) -> bytes:
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in _ALLOWED_THUMBNAIL_CONTENT_TYPES:
        raise MediaUrlPolicyError("Thumbnail response content type is not supported.")
    raw_length = response.headers.get("content-length")
    if raw_length is not None:
        try:
            content_length = int(raw_length)
        except ValueError as exc:
            raise MediaUrlPolicyError("Thumbnail response length is invalid.") from exc
        if content_length < 0 or content_length > EMBEDDING_DOWNLOAD_MAX_BYTES:
            raise MediaUrlPolicyError("Thumbnail response exceeds the allowed size.")

    content = bytearray()
    for chunk in response.iter_bytes(chunk_size=64 * 1024):
        content.extend(chunk)
        if len(content) > EMBEDDING_DOWNLOAD_MAX_BYTES:
            raise MediaUrlPolicyError("Thumbnail response exceeds the allowed size.")
    image = bytes(content)
    if not (
        image.startswith(b"\xff\xd8\xff")
        or image.startswith(b"\x89PNG\r\n\x1a\n")
        or image.startswith((b"GIF87a", b"GIF89a"))
        or (image.startswith(b"RIFF") and image[8:12] == b"WEBP")
    ):
        raise MediaUrlPolicyError("Thumbnail response is not a supported image.")
    return image


def enrich_stock_asset(
    *,
    asset,
    repository,
    clip_model_name: str,
    embedding_provider=None,
    workspace_id: object | None = None,
    project_id: object | None = None,
    timeout_seconds: float = EMBEDDING_DOWNLOAD_TIMEOUT_SECONDS,
    max_attempts: int = EMBEDDING_DOWNLOAD_MAX_ATTEMPTS,
):
    embedding_cache_version = getattr(
        embedding_provider,
        "cache_version",
        clip_model_name,
    )
    current_cache_key = build_stock_asset_embedding_cache_key(
        provider=asset.provider,
        provider_asset_id=asset.provider_asset_id,
        embedding_model_version=embedding_cache_version,
    )
    existing_cache_key = asset.raw_metadata.get("embedding_cache_key")
    preferred_revision = getattr(
        embedding_provider,
        "preferred_model_revision",
        None,
    )
    selected_revision = asset.raw_metadata.get("embedding_model_version")
    if (
        asset.raw_metadata.get("enrichment_status") == StockAssetEnrichmentStatus.COMPLETED.value
        and existing_cache_key == current_cache_key
        and isinstance(asset.raw_metadata.get("embedding"), list)
        and (preferred_revision is None or selected_revision == preferred_revision)
    ):
        logger.info(
            "stock_asset_enrichment_cache_hit",
            extra={
                "provider": asset.provider.value,
                "provider_asset_id": asset.provider_asset_id,
                "status": StockAssetEnrichmentStatus.COMPLETED.value,
                "cache_hit": True,
                "embedding_model_version": clip_model_name,
            },
        )
        repository.update_asset_metadata(
            asset=asset,
            raw_metadata={"enrichment_cache_hit": True},
        )
        return asset
    if asset.provider not in {StockProviderKey.PEXELS, StockProviderKey.PIXABAY}:
        return repository.update_asset_metadata(
            asset=asset,
            raw_metadata={
                "enrichment_status": StockAssetEnrichmentStatus.SKIPPED.value,
                "enrichment_failure_reason": None,
                "embedding_cache_key": current_cache_key,
            },
        )
    thumbnail_url = str(
        asset.raw_metadata.get("thumbnail_url")
        or asset.thumbnail_url
        or asset.raw_metadata.get("source_url")
        or ""
    ).strip()
    if not thumbnail_url.startswith(("http://", "https://")):
        return repository.update_asset_metadata(
            asset=asset,
            raw_metadata={
                "enrichment_status": StockAssetEnrichmentStatus.FAILED.value,
                "enrichment_failure_reason": "thumbnail_url_missing",
                "embedding_cache_key": current_cache_key,
                "enrichment_attempted_at": datetime.now(UTC).isoformat(),
            },
        )
    repository.update_asset_metadata(
        asset=asset,
        raw_metadata={
            "enrichment_status": StockAssetEnrichmentStatus.PROCESSING.value,
            "enrichment_failure_reason": None,
            "embedding_cache_key": current_cache_key,
            "enrichment_attempted_at": datetime.now(UTC).isoformat(),
            "enrichment_cache_hit": False,
        },
    )
    provider = embedding_provider or stock_embedding_provider(model_name=clip_model_name)
    started = time.perf_counter()
    last_reason = "embedding_unavailable"
    for attempt in range(1, max(1, max_attempts) + 1):
        try:
            with safe_http_stream(thumbnail_url, timeout=timeout_seconds) as response:
                response.raise_for_status()
                image_bytes = _read_bounded_thumbnail(response)
            embedding = _provider_embed_image(
                provider,
                image_bytes=image_bytes,
                source_hint=thumbnail_url,
                workspace_id=workspace_id,
                project_id=project_id,
            )
            if not embedding:
                last_reason = "embedding_empty"
                continue
            enriched = repository.update_asset_metadata(
                asset=asset,
                raw_metadata={
                    "embedding": embedding,
                    "embedding_model_version": provider.model_version,
                    "enrichment_status": StockAssetEnrichmentStatus.COMPLETED.value,
                    "enrichment_failure_reason": None,
                    "enrichment_completed_at": datetime.now(UTC).isoformat(),
                    "enrichment_attempts": attempt,
                    "enrichment_duration_ms": latency_ms_since(started),
                },
            )
            logger.info(
                "stock_asset_enrichment_completed",
                extra={
                    "provider": asset.provider.value,
                    "provider_asset_id": asset.provider_asset_id,
                    "status": StockAssetEnrichmentStatus.COMPLETED.value,
                    "cache_hit": False,
                    "embedding_model_version": provider.model_version,
                    "failure_reason": None,
                    "duration_ms": enriched.raw_metadata.get("enrichment_duration_ms"),
                },
            )
            return enriched
        except MediaUrlPolicyError:
            last_reason = "thumbnail_download_rejected"
        except httpx.TimeoutException:
            last_reason = "thumbnail_download_timeout"
        except httpx.HTTPStatusError:
            last_reason = "thumbnail_download_failed"
        except httpx.RequestError:
            last_reason = "thumbnail_download_failed"
        except Exception:
            last_reason = "embedding_generation_failed"
    failed = repository.update_asset_metadata(
        asset=asset,
        raw_metadata={
            "enrichment_status": StockAssetEnrichmentStatus.FAILED.value,
            "enrichment_failure_reason": last_reason,
            "enrichment_attempts": max(1, max_attempts),
            "enrichment_duration_ms": latency_ms_since(started),
        },
    )
    logger.warning(
        "stock_asset_enrichment_failed",
        extra={
            "provider": asset.provider.value,
            "provider_asset_id": asset.provider_asset_id,
            "status": StockAssetEnrichmentStatus.FAILED.value,
            "cache_hit": False,
            "embedding_model_version": clip_model_name,
            "failure_reason": last_reason,
            "duration_ms": failed.raw_metadata.get("enrichment_duration_ms"),
        },
    )
    return failed


def query_embedding(
    *,
    semantic_query: str,
    clip_model_name: str = "openai/clip-vit-base-patch32",
    embedding_provider=None,
    workspace_id: object | None = None,
    project_id: object | None = None,
) -> list[float]:
    if not semantic_query.strip():
        return []
    provider = embedding_provider or stock_embedding_provider(model_name=clip_model_name)
    return _provider_embed_text(
        provider,
        semantic_query,
        workspace_id=workspace_id,
        project_id=project_id,
    )


def _provider_embed_text(
    provider,
    text: str,
    *,
    workspace_id: object | None,
    project_id: object | None,
) -> list[float]:
    if hasattr(provider, "gateway"):
        return provider.embed_text(
            text,
            workspace_id=workspace_id,
            project_id=project_id,
        )
    return provider.embed_text(text)


def _provider_embed_image(
    provider,
    *,
    image_bytes: bytes,
    source_hint: str | None,
    workspace_id: object | None,
    project_id: object | None,
) -> list[float]:
    if hasattr(provider, "gateway"):
        return provider.embed_image(
            image_bytes=image_bytes,
            source_hint=source_hint,
            workspace_id=workspace_id,
            project_id=project_id,
        )
    return provider.embed_image(image_bytes=image_bytes, source_hint=source_hint)


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
