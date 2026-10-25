"""The declarative inference registry file schema and validator (plan Task 4).

The registry is a provider-neutral JSON document (``version: 1``) with three
lists: ``adapters`` (Section 2.2), ``models`` (Section 2.9) and ``routes``
(Section 2.3). :func:`load_registry_document` parses it strictly into frozen
dataclasses and :func:`validate_registry` applies every Section 2.6 rule.

Nothing here reads a secret value: secrets are referenced by name only, and the
loader never opens a secret file or bundle. Vendor names only ever appear in the
operator-supplied ``model_id``, ``base_url``, provider label and
``data_processor`` fields of a registry *file*; no vendor name appears in this
module or in any id it parses.

Public API (other Tier 1 tasks depend on these names):

- dataclasses :class:`AdapterRecord`, :class:`ModelRecord`, :class:`RouteRecord`
  with nested :class:`AuthConfig`, :class:`PricingRecord`, :class:`BudgetRecord`,
  :class:`HardCap`, :class:`Limits`, :class:`DataProcessor`, :class:`InputTransform`;
  :class:`RegistryDocument`; :class:`Diagnostic`; :class:`RegistryValidation`.
- :func:`load_registry_document`, :func:`validate_registry`.
- :func:`is_self_hosted`, :func:`route_locality`.
- :func:`to_model_route`.
"""

from __future__ import annotations

import ipaddress
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from app.core.local_audit import https_host_from_url
from app.schemas.billing import BillingPlanKey
from app.services.inference_capabilities import (
    InferenceCapability,
    coarse_family_for_capability,
    model_operation_for_capability,
)
from app.services.model_gateway import BUDGET_FEATURES, ModelRoute

REGISTRY_FORMAT_VERSION = 1

# Adapter types the registry format accepts (plan Section 2.2; Tasks 24/25 add
# ``huggingface_endpoint`` and ``aws_bedrock``).
_SUPPORTED_ADAPTER_TYPES = frozenset(
    {
        "openai_compatible",
        "azure_openai",
        "local",
        "fake",
        "generic_http",
        "async_job",
        "huggingface_endpoint",
        "aws_bedrock",
    }
)
# Reserved for a later tier; rejected now with ``adapter_type_not_available``.
_RESERVED_ADAPTER_TYPES = frozenset({"agent_pool"})
# Adapter types carrying a typed ``config`` block mirroring their config
# dataclass (plan Tasks 24/25). Each is validated by the adapter module's own
# ``validate_*`` function before a route is activated.
_CONFIGURED_ADAPTER_TYPES = frozenset({"huggingface_endpoint", "aws_bedrock"})
# ``aws_bedrock`` discovers credentials from the IAM chain (env/ECS/IMDS), so a
# remote Bedrock route needs no AI secret ref (plan Task 25 "the IAM path stores
# no static cloud key as an AI secret"). Every other remote adapter type still
# requires an auth secret ref.
_IAM_CREDENTIAL_ADAPTER_TYPES = frozenset({"aws_bedrock"})
_SELF_HOSTED_ADAPTER_TYPES = frozenset({"local", "agent_pool"})
_AUTH_TYPES = frozenset(
    {"none", "bearer", "custom_header", "azure_api_key", "oauth2_client_credentials"}
)
_NETWORKS = frozenset({"public", "private"})
_STRATEGIES = frozenset(
    {"native", "chat_prompt", "generic_mapping", "async_job", "local"}
)
_COMPUTE_REQUIREMENTS = frozenset({"cpu", "gpu", "remote", "any"})
_PRICING_STATUSES = frozenset({"priced", "free", "unknown"})
_MAPPING_ADAPTER_TYPES = frozenset({"generic_http", "async_job"})

# Offering metadata (plan Section 2.11, Task 29). ``labels`` is a closed set;
# ``plans`` are validated against the billing plan enum; ``quota_weight`` has a
# fixed inclusive range with a default of 1.0.
_OFFERING_LABELS = frozenset({"fast", "balanced", "quality", "economy", "local"})
_OFFERING_PLAN_KEYS = frozenset(plan.value for plan in BillingPlanKey)
_OFFERING_QUOTA_WEIGHT_MIN = 0.1
_OFFERING_QUOTA_WEIGHT_MAX = 100.0
_OFFERING_QUOTA_WEIGHT_DEFAULT = 1.0

# Capabilities that must have at least one valid enabled route for startup
# (plan invariant 3, Section 2.6).
REQUIRED_CAPABILITIES = frozenset(
    {
        InferenceCapability.PLANNING_CHAT,
        InferenceCapability.TRANSCRIPTION_SPEECH_TO_TEXT,
    }
)

# Capability/adapter-type support matrix (plan Section 2.10 table). Each adapter
# type lists the capabilities it may serve.
_SUPPORT_MATRIX: dict[str, frozenset[InferenceCapability]] = {
    "openai_compatible": frozenset(
        {
            InferenceCapability.PLANNING_CHAT,
            InferenceCapability.TRANSCRIPTION_SPEECH_TO_TEXT,
            InferenceCapability.VISION_CAPTION,
            InferenceCapability.AUDIO_EMOTION,
            InferenceCapability.EMBEDDINGS_TEXT,
            InferenceCapability.EMBEDDINGS_IMAGE,
            InferenceCapability.IMAGE_GENERATION,
        }
    ),
    "azure_openai": frozenset(
        {
            InferenceCapability.PLANNING_CHAT,
            InferenceCapability.TRANSCRIPTION_SPEECH_TO_TEXT,
            InferenceCapability.VISION_CAPTION,
            InferenceCapability.AUDIO_EMOTION,
            InferenceCapability.EMBEDDINGS_TEXT,
            InferenceCapability.EMBEDDINGS_IMAGE,
            InferenceCapability.IMAGE_GENERATION,
        }
    ),
    "local": frozenset(
        {
            InferenceCapability.MATTE_SEGMENTATION,
            InferenceCapability.AUDIO_STEM_SEPARATION,
            InferenceCapability.AUDIO_SOUND_CLASSIFICATION,
            InferenceCapability.EMBEDDINGS_TEXT,
            InferenceCapability.EMBEDDINGS_IMAGE,
            InferenceCapability.VISION_CAPTION,
            InferenceCapability.VISION_OBJECT_DETECTION,
            InferenceCapability.AUDIO_DIARIZATION,
            InferenceCapability.AUDIO_EMOTION,
            InferenceCapability.TRANSCRIPTION_SPEECH_TO_TEXT,
        }
    ),
    "fake": frozenset(InferenceCapability),
    "generic_http": frozenset(
        {
            InferenceCapability.VISION_OBJECT_DETECTION,
            InferenceCapability.AUDIO_DIARIZATION,
            # Tier 3 typed outputs (plan Task 28) let generic_http also serve
            # synchronous transcription, captions and text/image embeddings on
            # APIs that are not OpenAI-compatible.
            InferenceCapability.TRANSCRIPTION_SPEECH_TO_TEXT,
            InferenceCapability.VISION_CAPTION,
            InferenceCapability.EMBEDDINGS_TEXT,
            InferenceCapability.EMBEDDINGS_IMAGE,
        }
    ),
    "async_job": frozenset(
        {
            InferenceCapability.AUDIO_DIARIZATION,
            InferenceCapability.AUDIO_STEM_SEPARATION,
            InferenceCapability.VIDEO_GENERATION_SUBMIT,
            InferenceCapability.VIDEO_GENERATION_STATUS,
        }
    ),
    # Hugging Face Inference Endpoint (plan Task 24). The three serving modes
    # (tgi/tei/pipeline) together cover: tgi -> planning.chat; tei ->
    # embeddings.text; pipeline -> vision.caption (image-to-text),
    # transcription.speech_to_text (ASR with word timestamps), sound
    # classification and emotion (audio-classification) and embeddings.text
    # (feature-extraction). The per-mode capability is enforced in the adapter.
    "huggingface_endpoint": frozenset(
        {
            InferenceCapability.PLANNING_CHAT,
            InferenceCapability.EMBEDDINGS_TEXT,
            InferenceCapability.VISION_CAPTION,
            InferenceCapability.TRANSCRIPTION_SPEECH_TO_TEXT,
            InferenceCapability.AUDIO_SOUND_CLASSIFICATION,
            InferenceCapability.AUDIO_EMOTION,
        }
    ),
    # AWS Bedrock (plan Task 25). Converse serves planning.chat and
    # vision.caption; InvokeModel serves embeddings.text.
    "aws_bedrock": frozenset(
        {
            InferenceCapability.PLANNING_CHAT,
            InferenceCapability.VISION_CAPTION,
            InferenceCapability.EMBEDDINGS_TEXT,
        }
    ),
}


# -- Diagnostic codes. Tests assert these exact snake_case strings. ----------
class DiagnosticCode:
    """Every stable diagnostic code the loader and validator emit."""

    # Loader / structural
    INVALID_JSON = "invalid_json"
    INVALID_DOCUMENT = "invalid_document"
    UNSUPPORTED_VERSION = "unsupported_version"
    UNKNOWN_KEY = "unknown_key"
    INVALID_FIELD = "invalid_field"
    MISSING_FIELD = "missing_field"
    DUPLICATE_ID = "duplicate_id"
    UNKNOWN_REFERENCE = "unknown_reference"
    # Adapter format
    ADAPTER_TYPE_NOT_AVAILABLE = "adapter_type_not_available"
    ADAPTER_TYPE_UNKNOWN = "adapter_type_unknown"
    BASE_URL_REQUIRED = "base_url_required"
    BASE_URL_INVALID = "base_url_invalid"
    BASE_URL_SCHEME_INVALID = "base_url_scheme_invalid"
    BASE_URL_HAS_CREDENTIALS = "base_url_has_credentials"
    BASE_URL_HAS_QUERY = "base_url_has_query"
    BASE_URL_PRIVATE_IP_PUBLIC_NETWORK = "base_url_private_ip_public_network"
    BASE_URL_PUBLIC_IP_PRIVATE_NETWORK = "base_url_public_ip_private_network"
    SECRET_REF_INVALID = "secret_ref_invalid"
    SECRET_REF_NOT_AVAILABLE = "secret_ref_not_available"
    SECRET_REF_ENV_REJECTED = "secret_ref_env_rejected"
    SECRET_REF_ENV_WARNING = "secret_ref_env_warning"
    AUTH_TYPE_UNKNOWN = "auth_type_unknown"
    AUTH_MISSING_HEADER_NAME = "auth_missing_header_name"
    AUTH_MISSING_OAUTH_FIELDS = "auth_missing_oauth_fields"
    API_VERSION_NOT_ALLOWED = "api_version_not_allowed"
    EXTRA_BODY_NOT_ALLOWED = "extra_body_not_allowed"
    # Typed adapter config (plan Tasks 24/25). A ``config`` block is required for
    # the configured adapter types and rejected on the others; its contents are
    # validated by the adapter module's own validation function.
    ADAPTER_CONFIG_REQUIRED = "adapter_config_required"
    ADAPTER_CONFIG_NOT_ALLOWED = "adapter_config_not_allowed"
    ADAPTER_CONFIG_INVALID = "adapter_config_invalid"
    # Route format / tier gates
    CAPABILITY_UNKNOWN = "capability_unknown"
    CAPABILITY_LEGACY_ALIAS = "capability_legacy_alias"
    STRATEGY_UNKNOWN = "strategy_unknown"
    COMPUTE_REQUIREMENT_UNKNOWN = "compute_requirement_unknown"
    OPTION_NOT_AVAILABLE = "option_not_available"
    SCOPE_WORKSPACE_DISABLED = "scope_workspace_disabled"
    SCOPE_INVALID = "scope_invalid"
    # Section 2.6 rules
    REQUIRED_CAPABILITY_MISSING = "required_capability_missing"
    REVISION_PROVIDER_MANAGED = "revision_provider_managed"
    REVISION_PROVIDER_MANAGED_NOT_ALLOWED = "revision_provider_managed_not_allowed"
    REVISION_PROVIDER_MANAGED_SELF_HOSTED = "revision_provider_managed_self_hosted"
    REVISION_FLOATING = "revision_floating"
    REVISION_EQUALS_MODEL_ID = "revision_equals_model_id"
    LICENCE_REQUIRED = "licence_required"
    LICENCE_MISSING = "licence_missing"
    LICENCE_NONCOMMERCIAL = "licence_noncommercial"
    DATA_PROCESSOR_MISSING = "data_processor_missing"
    AUTH_REQUIRED = "auth_required"
    AUTH_NONE_REMOTE = "auth_none_remote"
    PRICING_ZERO_NOT_FREE = "pricing_zero_not_free"
    PRICING_UNKNOWN_NEEDS_HARD_CAP = "pricing_unknown_needs_hard_cap"
    FREE_PLANNING_REMOTE = "free_planning_remote"
    FAKE_ADAPTER_IN_PRODUCTION = "fake_adapter_in_production"
    ROUTE_REQUIRES_GPU = "route_requires_gpu"
    CAPABILITY_ADAPTER_UNSUPPORTED = "capability_adapter_unsupported"
    EMBEDDING_SPACE_MISMATCH = "embedding_space_mismatch"
    MAPPING_REQUIRED = "mapping_required"
    MAPPING_NOT_ALLOWED = "mapping_not_allowed"
    MAPPING_INVALID = "mapping_invalid"
    # Offering metadata (plan Task 29, Section 2.11). The user-model-choice
    # block on a route. ``offering_multiple_defaults`` is an error; the others
    # are per-route errors except ``offering_default_not_selectable``, a warning.
    OFFERING_INVALID = "offering_invalid"
    OFFERING_MULTIPLE_DEFAULTS = "offering_multiple_defaults"
    OFFERING_PLAN_UNKNOWN = "offering_plan_unknown"
    OFFERING_QUOTA_WEIGHT_OUT_OF_RANGE = "offering_quota_weight_out_of_range"
    OFFERING_DEFAULT_NOT_SELECTABLE = "offering_default_not_selectable"
    # Runtime build (plan Task 6): a ``local`` route whose runner has no adapter
    # factory yet (Task 14 provides them) is dropped at runtime.
    LOCAL_RUNNER_UNAVAILABLE = "local_runner_unavailable"
    # Registry fragment merge (plan Task 14): an id defined in more than one
    # fragment when AI_REGISTRY_FILE lists several files.
    REGISTRY_FRAGMENT_COLLISION = "registry_fragment_collision"


# The complete list of codes, as a module-level constant for tests.
DIAGNOSTIC_CODES: frozenset[str] = frozenset(
    value
    for name, value in vars(DiagnosticCode).items()
    if not name.startswith("_") and isinstance(value, str)
)

# Tier 2 route options the file format rejects now (plan Section 2.10 tier gate).
# Tier 2/3 route-level options the file format still rejects (plan Section 2.10
# tier gate). ``embeddings_input_style`` moved to an adapter option in Tier 2
# (Task 12), so the set is currently empty; the gate stays for future options.
_TIER2_ROUTE_OPTIONS: frozenset[str] = frozenset()

# Accepted values for the openai_compatible/azure_openai ``embeddings_input_style``
# adapter option (plan Section 2.10, Task 12).
_EMBEDDINGS_INPUT_STYLES = frozenset({"openai", "typed_objects"})


@dataclass(frozen=True)
class Diagnostic:
    """A single validation finding (plan Section 2.6)."""

    object_kind: str
    object_id: str
    severity: str  # "error" | "warning"
    code: str
    message: str
    path: str

    @property
    def is_error(self) -> bool:
        return self.severity == "error"


@dataclass(frozen=True)
class AuthConfig:
    type: str
    secret_ref: str | None = None
    header_name: str | None = None
    token_url: str | None = None
    client_id: str | None = None
    client_secret_ref: str | None = None
    scope: str | None = None


@dataclass(frozen=True)
class DataProcessor:
    display_name: str
    purpose: str
    data_categories: tuple[str, ...]
    policy_url: str


@dataclass(frozen=True)
class AdapterRecord:
    id: str
    type: str
    base_url: str | None = None
    network: str = "public"
    auth: AuthConfig = field(default_factory=lambda: AuthConfig(type="none"))
    timeout_s: float | None = None
    health_method: str | None = None
    health_path: str | None = None
    extra_headers: Mapping[str, str] = field(default_factory=dict)
    extra_body: Mapping[str, object] = field(default_factory=dict)
    api_version: str | None = None
    data_processor: DataProcessor | None = None
    config: Mapping[str, object] = field(default_factory=dict)
    # Embedding request shape for openai_compatible/azure_openai (plan Section
    # 2.10 closed option, Task 12): "openai" (flat string inputs) or
    # "typed_objects" (text/image objects in one shared vector space).
    embeddings_input_style: str = "openai"


@dataclass(frozen=True)
class ModelRecord:
    id: str
    model_id: str
    revision: str
    licence: str | None = None
    commercial_use: bool | None = None
    source_type: str | None = None
    source_url: str | None = None
    capabilities: tuple[str, ...] = ()
    min_vram_gb: float | None = None
    weights_sha256: str | None = None
    verified_at: str | None = None


@dataclass(frozen=True)
class HardCap:
    max_requests_per_month: int | None = None
    max_units_per_month: int | None = None

    @property
    def has_positive_cap(self) -> bool:
        return bool(
            (self.max_requests_per_month or 0) > 0
            or (self.max_units_per_month or 0) > 0
        )


@dataclass(frozen=True)
class PricingRecord:
    status: str = "priced"
    unit: str | None = None
    input_per_million_usd: float | None = None
    output_per_million_usd: float | None = None
    per_request_usd: float | None = None
    per_unit_usd: float | None = None
    minimum_billable_units: int = 0


@dataclass(frozen=True)
class BudgetRecord:
    feature: str | None = None
    monthly_usd: float | None = None
    hard_cap: HardCap = field(default_factory=HardCap)


@dataclass(frozen=True)
class Limits:
    max_batch: int | None = None
    max_request_bytes: int | None = None
    max_duration_s: float | None = None
    max_output_units: int | None = None


@dataclass(frozen=True)
class InputTransform:
    audio_codec: str | None = None
    audio_sample_rate: int | None = None
    audio_channels: int | None = None


@dataclass(frozen=True)
class Offering:
    """User-model-choice metadata on a route (plan Section 2.11, Task 29).

    The option id is the route id. ``display_name`` and ``description`` are
    operator display data and may name a model or vendor (the scanner treats
    them like ``data_processor``). ``labels`` is a closed set; ``plans`` lists
    the billing plans allowed to pick the route (empty means every plan may);
    ``quota_weight`` multiplies the feature-quota units of calls the route
    serves. A route is only user-visible when ``selectable`` is true.
    """

    selectable: bool = False
    display_name: str | None = None
    description: str | None = None
    labels: tuple[str, ...] = ()
    plans: tuple[str, ...] = ()
    quota_weight: float = _OFFERING_QUOTA_WEIGHT_DEFAULT
    default: bool = False


@dataclass(frozen=True)
class RouteRecord:
    id: str
    capability: InferenceCapability
    adapter_id: str
    model_id: str
    model_revision: str
    model_ref: str | None = None
    allow_provider_managed_revision: bool = False
    priority: int = 100
    strategy: str | None = None
    provider: str | None = None
    pricing: PricingRecord = field(default_factory=PricingRecord)
    explicitly_free: bool = False
    budget: BudgetRecord = field(default_factory=BudgetRecord)
    timeout_s: float | None = None
    compute_requirement: str = "any"
    requires_gpu: bool = False
    limits: Limits = field(default_factory=Limits)
    enabled: bool = True
    scope: str = "global"
    embedding_space: str | None = None
    params: Mapping[str, object] = field(default_factory=dict)
    mapping: Mapping[str, object] | None = None
    upload_filename: str | None = None
    label_aliases: Mapping[str, str] = field(default_factory=dict)
    input_transform: InputTransform | None = None
    offering: Offering | None = None
    source: str = "file"


@dataclass(frozen=True)
class RegistryDocument:
    version: int
    adapters: tuple[AdapterRecord, ...]
    models: tuple[ModelRecord, ...]
    routes: tuple[RouteRecord, ...]

    def adapter_by_id(self, adapter_id: str) -> AdapterRecord | None:
        return next((a for a in self.adapters if a.id == adapter_id), None)

    def model_by_id(self, model_id: str) -> ModelRecord | None:
        return next((m for m in self.models if m.id == model_id), None)


@dataclass(frozen=True)
class RegistryValidation:
    document: RegistryDocument | None
    diagnostics: tuple[Diagnostic, ...]
    valid_route_ids: tuple[str, ...]
    dropped_route_ids: tuple[str, ...]

    def errors(self) -> tuple[Diagnostic, ...]:
        return tuple(d for d in self.diagnostics if d.is_error)

    def warnings(self) -> tuple[Diagnostic, ...]:
        return tuple(d for d in self.diagnostics if not d.is_error)



# -- Allowed key sets (strict parsing rejects anything else). ----------------
_TOP_LEVEL_KEYS = frozenset({"version", "adapters", "models", "routes"})
_ADAPTER_KEYS = frozenset(
    {
        "id",
        "type",
        "base_url",
        "network",
        "auth",
        "timeout_s",
        "health",
        "extra_headers",
        "extra_body",
        "api_version",
        "data_processor",
        "config",
        "embeddings_input_style",
    }
)
_AUTH_KEYS = frozenset(
    {
        "type",
        "secret_ref",
        "header_name",
        "token_url",
        "client_id",
        "client_secret_ref",
        "scope",
    }
)
_HEALTH_KEYS = frozenset({"method", "path"})
_DATA_PROCESSOR_KEYS = frozenset(
    {"display_name", "purpose", "data_categories", "policy_url"}
)
_MODEL_KEYS = frozenset(
    {
        "id",
        "model_id",
        "revision",
        "licence",
        "commercial_use",
        "source_type",
        "source_url",
        "capabilities",
        "min_vram_gb",
        "weights_sha256",
        "verified_at",
    }
)
_ROUTE_KEYS = frozenset(
    {
        "id",
        "capability",
        "adapter_id",
        "model_id",
        "model_ref",
        "model_revision",
        "allow_provider_managed_revision",
        "priority",
        "strategy",
        "provider",
        "pricing",
        "explicitly_free",
        "budget",
        "timeout_s",
        "compute_requirement",
        "requires_gpu",
        "limits",
        "enabled",
        "scope",
        "embedding_space",
        "params",
        "mapping",
        "upload_filename",
        "label_aliases",
        "input_transform",
        "offering",
    }
)
_OFFERING_KEYS = frozenset(
    {
        "selectable",
        "display_name",
        "description",
        "labels",
        "plans",
        "quota_weight",
        "default",
    }
)
_PRICING_KEYS = frozenset(
    {
        "status",
        "unit",
        "input_per_million_usd",
        "output_per_million_usd",
        "per_request_usd",
        "per_unit_usd",
        "minimum_billable_units",
    }
)
_BUDGET_KEYS = frozenset({"feature", "monthly_usd", "hard_cap"})
_HARD_CAP_KEYS = frozenset({"max_requests_per_month", "max_units_per_month"})
_LIMITS_KEYS = frozenset(
    {"max_batch", "max_request_bytes", "max_duration_s", "max_output_units"}
)
_INPUT_TRANSFORM_KEYS = frozenset({"audio"})
_INPUT_TRANSFORM_AUDIO_KEYS = frozenset({"codec", "sample_rate", "channels"})

# Legacy capability aliases that are accepted by ``parse_capability`` elsewhere
# but rejected in the registry *file* format (plan Section 2.3: "reject legacy
# aliases in the file format").
_LEGACY_CAPABILITY_ALIASES = frozenset(
    {
        "vision.prompt",
        "audio.prompt",
        "vision.matte_segmentation",
        "generation.image",
        "generation.video_submit",
        "generation.video_poll",
    }
)


class _LoadError(Exception):
    """Raised internally to abort parsing a record with a diagnostic."""

    def __init__(self, diagnostic: Diagnostic) -> None:
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic


def _diag(
    kind: str, obj_id: str, severity: str, code: str, message: str, path: str
) -> Diagnostic:
    return Diagnostic(kind, obj_id, severity, code, message, path)


def _err(kind: str, obj_id: str, code: str, message: str, path: str) -> _LoadError:
    return _LoadError(_diag(kind, obj_id, "error", code, message, path))


def _require_mapping(value: object, kind: str, obj_id: str, path: str) -> Mapping:
    if not isinstance(value, Mapping):
        raise _err(kind, obj_id, DiagnosticCode.INVALID_FIELD, f"{path} must be an object", path)
    return value


def _reject_unknown_keys(
    value: Mapping, allowed: frozenset[str], kind: str, obj_id: str, path: str
) -> None:
    for key in value:
        if key not in allowed:
            raise _err(
                kind,
                obj_id,
                DiagnosticCode.UNKNOWN_KEY,
                f"unknown key {key!r} at {path}",
                f"{path}.{key}",
            )


def _str_field(
    value: Mapping,
    key: str,
    kind: str,
    obj_id: str,
    path: str,
    *,
    required: bool = False,
) -> str | None:
    if key not in value or value[key] is None:
        if required:
            raise _err(
                kind,
                obj_id,
                DiagnosticCode.MISSING_FIELD,
                f"{path}.{key} is required",
                f"{path}.{key}",
            )
        return None
    raw = value[key]
    if not isinstance(raw, str) or not raw.strip():
        raise _err(
            kind,
            obj_id,
            DiagnosticCode.INVALID_FIELD,
            f"{path}.{key} must be a non-empty string",
            f"{path}.{key}",
        )
    return raw


def _bool_field(
    value: Mapping, key: str, kind: str, obj_id: str, path: str, default: bool
) -> bool:
    if key not in value:
        return default
    raw = value[key]
    if not isinstance(raw, bool):
        raise _err(
            kind,
            obj_id,
            DiagnosticCode.INVALID_FIELD,
            f"{path}.{key} must be a JSON boolean",
            f"{path}.{key}",
        )
    return raw


def _bool_or_none(
    value: Mapping, key: str, kind: str, obj_id: str, path: str
) -> bool | None:
    if key not in value or value[key] is None:
        return None
    raw = value[key]
    if not isinstance(raw, bool):
        raise _err(
            kind,
            obj_id,
            DiagnosticCode.INVALID_FIELD,
            f"{path}.{key} must be a JSON boolean",
            f"{path}.{key}",
        )
    return raw


def _number_field(
    value: Mapping, key: str, kind: str, obj_id: str, path: str
) -> float | None:
    if key not in value or value[key] is None:
        return None
    raw = value[key]
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise _err(
            kind,
            obj_id,
            DiagnosticCode.INVALID_FIELD,
            f"{path}.{key} must be a number",
            f"{path}.{key}",
        )
    return float(raw)


def _int_field(
    value: Mapping, key: str, kind: str, obj_id: str, path: str
) -> int | None:
    if key not in value or value[key] is None:
        return None
    raw = value[key]
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise _err(
            kind,
            obj_id,
            DiagnosticCode.INVALID_FIELD,
            f"{path}.{key} must be an integer",
            f"{path}.{key}",
        )
    return raw


def _parse_auth(raw: object, obj_id: str, path: str) -> AuthConfig:
    value = _require_mapping(raw, "adapter", obj_id, path)
    _reject_unknown_keys(value, _AUTH_KEYS, "adapter", obj_id, path)
    auth_type = _str_field(value, "type", "adapter", obj_id, path, required=True)
    if auth_type not in _AUTH_TYPES:
        raise _err(
            "adapter",
            obj_id,
            DiagnosticCode.AUTH_TYPE_UNKNOWN,
            f"unknown auth type {auth_type!r}",
            f"{path}.type",
        )
    return AuthConfig(
        type=auth_type,
        secret_ref=_str_field(value, "secret_ref", "adapter", obj_id, path),
        header_name=_str_field(value, "header_name", "adapter", obj_id, path),
        token_url=_str_field(value, "token_url", "adapter", obj_id, path),
        client_id=_str_field(value, "client_id", "adapter", obj_id, path),
        client_secret_ref=_str_field(
            value, "client_secret_ref", "adapter", obj_id, path
        ),
        scope=_str_field(value, "scope", "adapter", obj_id, path),
    )


def _parse_data_processor(raw: object, obj_id: str, path: str) -> DataProcessor:
    value = _require_mapping(raw, "adapter", obj_id, path)
    _reject_unknown_keys(value, _DATA_PROCESSOR_KEYS, "adapter", obj_id, path)
    display_name = _str_field(
        value, "display_name", "adapter", obj_id, path, required=True
    )
    purpose = _str_field(value, "purpose", "adapter", obj_id, path, required=True)
    policy_url = _str_field(value, "policy_url", "adapter", obj_id, path, required=True)
    categories_raw = value.get("data_categories", [])
    if not isinstance(categories_raw, list) or not all(
        isinstance(item, str) for item in categories_raw
    ):
        raise _err(
            "adapter",
            obj_id,
            DiagnosticCode.INVALID_FIELD,
            f"{path}.data_categories must be a list of strings",
            f"{path}.data_categories",
        )
    assert display_name is not None and purpose is not None and policy_url is not None
    return DataProcessor(
        display_name=display_name,
        purpose=purpose,
        data_categories=tuple(categories_raw),
        policy_url=policy_url,
    )


def _parse_adapter(raw: object, index: int) -> AdapterRecord:
    path = f"adapters[{index}]"
    value = _require_mapping(raw, "adapter", f"#{index}", path)
    obj_id = _str_field(value, "id", "adapter", f"#{index}", path, required=True) or f"#{index}"
    _reject_unknown_keys(value, _ADAPTER_KEYS, "adapter", obj_id, path)
    adapter_type = _str_field(value, "type", "adapter", obj_id, path, required=True)
    if adapter_type in _RESERVED_ADAPTER_TYPES:
        raise _err(
            "adapter",
            obj_id,
            DiagnosticCode.ADAPTER_TYPE_NOT_AVAILABLE,
            f"adapter type {adapter_type!r} is reserved and not available",
            f"{path}.type",
        )
    if adapter_type not in _SUPPORTED_ADAPTER_TYPES:
        raise _err(
            "adapter",
            obj_id,
            DiagnosticCode.ADAPTER_TYPE_UNKNOWN,
            f"unknown adapter type {adapter_type!r}",
            f"{path}.type",
        )
    network = _str_field(value, "network", "adapter", obj_id, path) or "public"
    if network not in _NETWORKS:
        raise _err(
            "adapter",
            obj_id,
            DiagnosticCode.INVALID_FIELD,
            f"{path}.network must be one of {sorted(_NETWORKS)}",
            f"{path}.network",
        )
    if adapter_type != "azure_openai" and "api_version" in value:
        raise _err(
            "adapter",
            obj_id,
            DiagnosticCode.API_VERSION_NOT_ALLOWED,
            "api_version is only allowed on azure_openai adapters",
            f"{path}.api_version",
        )
    # Typed adapter config block (plan Tasks 24/25). Required for the configured
    # adapter types, rejected on the others. Structural shape only here; the
    # adapter module's ``validate_*`` runs in the validator.
    config = value.get("config", {})
    if adapter_type in _CONFIGURED_ADAPTER_TYPES:
        if "config" not in value or value["config"] is None:
            raise _err(
                "adapter",
                obj_id,
                DiagnosticCode.ADAPTER_CONFIG_REQUIRED,
                f"adapter type {adapter_type!r} requires a config block",
                f"{path}.config",
            )
        if not isinstance(config, Mapping):
            raise _err(
                "adapter",
                obj_id,
                DiagnosticCode.ADAPTER_CONFIG_INVALID,
                f"{path}.config must be an object",
                f"{path}.config",
            )
    elif "config" in value:
        raise _err(
            "adapter",
            obj_id,
            DiagnosticCode.ADAPTER_CONFIG_NOT_ALLOWED,
            f"config is only allowed on adapter types {sorted(_CONFIGURED_ADAPTER_TYPES)}",
            f"{path}.config",
        )
    extra_body = value.get("extra_body", {})
    if "extra_body" in value and adapter_type not in {"openai_compatible", "azure_openai"}:
        raise _err(
            "adapter",
            obj_id,
            DiagnosticCode.EXTRA_BODY_NOT_ALLOWED,
            "extra_body is only allowed on openai_compatible/azure_openai adapters",
            f"{path}.extra_body",
        )
    if not isinstance(extra_body, Mapping):
        raise _err(
            "adapter",
            obj_id,
            DiagnosticCode.INVALID_FIELD,
            f"{path}.extra_body must be an object",
            f"{path}.extra_body",
        )
    extra_headers = value.get("extra_headers", {})
    if not isinstance(extra_headers, Mapping) or not all(
        isinstance(v, str) for v in extra_headers.values()
    ):
        raise _err(
            "adapter",
            obj_id,
            DiagnosticCode.INVALID_FIELD,
            f"{path}.extra_headers must be an object of strings",
            f"{path}.extra_headers",
        )
    embeddings_input_style = (
        _str_field(value, "embeddings_input_style", "adapter", obj_id, path) or "openai"
    )
    if "embeddings_input_style" in value and adapter_type not in {
        "openai_compatible",
        "azure_openai",
    }:
        raise _err(
            "adapter",
            obj_id,
            DiagnosticCode.OPTION_NOT_AVAILABLE,
            "embeddings_input_style is only allowed on "
            "openai_compatible/azure_openai adapters",
            f"{path}.embeddings_input_style",
        )
    if embeddings_input_style not in _EMBEDDINGS_INPUT_STYLES:
        raise _err(
            "adapter",
            obj_id,
            DiagnosticCode.OPTION_NOT_AVAILABLE,
            f"embeddings_input_style must be one of {sorted(_EMBEDDINGS_INPUT_STYLES)}",
            f"{path}.embeddings_input_style",
        )
    health_method: str | None = None
    health_path: str | None = None
    if "health" in value:
        health = _require_mapping(value["health"], "adapter", obj_id, f"{path}.health")
        _reject_unknown_keys(health, _HEALTH_KEYS, "adapter", obj_id, f"{path}.health")
        health_method = _str_field(health, "method", "adapter", obj_id, f"{path}.health")
        health_path = _str_field(health, "path", "adapter", obj_id, f"{path}.health")
    auth = (
        _parse_auth(value["auth"], obj_id, f"{path}.auth")
        if "auth" in value
        else AuthConfig(type="none")
    )
    data_processor = (
        _parse_data_processor(
            value["data_processor"], obj_id, f"{path}.data_processor"
        )
        if "data_processor" in value and value["data_processor"] is not None
        else None
    )
    assert adapter_type is not None
    return AdapterRecord(
        id=obj_id,
        type=adapter_type,
        base_url=_str_field(value, "base_url", "adapter", obj_id, path),
        network=network,
        auth=auth,
        timeout_s=_number_field(value, "timeout_s", "adapter", obj_id, path),
        health_method=health_method,
        health_path=health_path,
        extra_headers=dict(extra_headers),
        extra_body=dict(extra_body),
        api_version=_str_field(value, "api_version", "adapter", obj_id, path),
        data_processor=data_processor,
        config=dict(config) if isinstance(config, Mapping) else {},
        embeddings_input_style=embeddings_input_style,
    )


def _parse_model(raw: object, index: int) -> ModelRecord:
    path = f"models[{index}]"
    value = _require_mapping(raw, "model", f"#{index}", path)
    obj_id = _str_field(value, "id", "model", f"#{index}", path, required=True) or f"#{index}"
    _reject_unknown_keys(value, _MODEL_KEYS, "model", obj_id, path)
    model_id = _str_field(value, "model_id", "model", obj_id, path, required=True)
    revision = _str_field(value, "revision", "model", obj_id, path, required=True)
    capabilities_raw = value.get("capabilities", [])
    if not isinstance(capabilities_raw, list) or not all(
        isinstance(item, str) for item in capabilities_raw
    ):
        raise _err(
            "model",
            obj_id,
            DiagnosticCode.INVALID_FIELD,
            f"{path}.capabilities must be a list of strings",
            f"{path}.capabilities",
        )
    assert model_id is not None and revision is not None
    return ModelRecord(
        id=obj_id,
        model_id=model_id,
        revision=revision,
        licence=_str_field(value, "licence", "model", obj_id, path),
        commercial_use=_bool_or_none(value, "commercial_use", "model", obj_id, path),
        source_type=_str_field(value, "source_type", "model", obj_id, path),
        source_url=_str_field(value, "source_url", "model", obj_id, path),
        capabilities=tuple(capabilities_raw),
        min_vram_gb=_number_field(value, "min_vram_gb", "model", obj_id, path),
        weights_sha256=_str_field(value, "weights_sha256", "model", obj_id, path),
        verified_at=_str_field(value, "verified_at", "model", obj_id, path),
    )


def _parse_pricing(raw: object, obj_id: str, path: str) -> PricingRecord:
    value = _require_mapping(raw, "route", obj_id, path)
    _reject_unknown_keys(value, _PRICING_KEYS, "route", obj_id, path)
    status = _str_field(value, "status", "route", obj_id, path) or "priced"
    if status not in _PRICING_STATUSES:
        raise _err(
            "route",
            obj_id,
            DiagnosticCode.INVALID_FIELD,
            f"{path}.status must be one of {sorted(_PRICING_STATUSES)}",
            f"{path}.status",
        )
    minimum = _int_field(value, "minimum_billable_units", "route", obj_id, path) or 0
    return PricingRecord(
        status=status,
        unit=_str_field(value, "unit", "route", obj_id, path),
        input_per_million_usd=_number_field(
            value, "input_per_million_usd", "route", obj_id, path
        ),
        output_per_million_usd=_number_field(
            value, "output_per_million_usd", "route", obj_id, path
        ),
        per_request_usd=_number_field(value, "per_request_usd", "route", obj_id, path),
        per_unit_usd=_number_field(value, "per_unit_usd", "route", obj_id, path),
        minimum_billable_units=minimum,
    )


def _parse_hard_cap(raw: object, obj_id: str, path: str) -> HardCap:
    value = _require_mapping(raw, "route", obj_id, path)
    _reject_unknown_keys(value, _HARD_CAP_KEYS, "route", obj_id, path)
    return HardCap(
        max_requests_per_month=_int_field(
            value, "max_requests_per_month", "route", obj_id, path
        ),
        max_units_per_month=_int_field(
            value, "max_units_per_month", "route", obj_id, path
        ),
    )


def _parse_budget(raw: object, obj_id: str, path: str) -> BudgetRecord:
    value = _require_mapping(raw, "route", obj_id, path)
    _reject_unknown_keys(value, _BUDGET_KEYS, "route", obj_id, path)
    hard_cap = (
        _parse_hard_cap(value["hard_cap"], obj_id, f"{path}.hard_cap")
        if "hard_cap" in value and value["hard_cap"] is not None
        else HardCap()
    )
    feature = _str_field(value, "feature", "route", obj_id, path)
    if feature is not None and feature not in BUDGET_FEATURES:
        raise _err(
            "route",
            obj_id,
            DiagnosticCode.INVALID_FIELD,
            f"{path}.feature must be one of {sorted(BUDGET_FEATURES)}",
            f"{path}.feature",
        )
    return BudgetRecord(
        feature=feature,
        monthly_usd=_number_field(value, "monthly_usd", "route", obj_id, path),
        hard_cap=hard_cap,
    )


def _parse_limits(raw: object, obj_id: str, path: str) -> Limits:
    value = _require_mapping(raw, "route", obj_id, path)
    _reject_unknown_keys(value, _LIMITS_KEYS, "route", obj_id, path)
    return Limits(
        max_batch=_int_field(value, "max_batch", "route", obj_id, path),
        max_request_bytes=_int_field(value, "max_request_bytes", "route", obj_id, path),
        max_duration_s=_number_field(value, "max_duration_s", "route", obj_id, path),
        max_output_units=_int_field(value, "max_output_units", "route", obj_id, path),
    )


def _parse_input_transform(raw: object, obj_id: str, path: str) -> InputTransform:
    value = _require_mapping(raw, "route", obj_id, path)
    _reject_unknown_keys(value, _INPUT_TRANSFORM_KEYS, "route", obj_id, path)
    if "audio" not in value:
        return InputTransform()
    audio = _require_mapping(value["audio"], "route", obj_id, f"{path}.audio")
    _reject_unknown_keys(
        audio, _INPUT_TRANSFORM_AUDIO_KEYS, "route", obj_id, f"{path}.audio"
    )
    return InputTransform(
        audio_codec=_str_field(audio, "codec", "route", obj_id, f"{path}.audio"),
        audio_sample_rate=_int_field(
            audio, "sample_rate", "route", obj_id, f"{path}.audio"
        ),
        audio_channels=_int_field(audio, "channels", "route", obj_id, f"{path}.audio"),
    )


def _parse_offering(raw: object, obj_id: str, path: str) -> Offering:
    """Parse the Section 2.11 ``offering`` block (plan Task 29).

    Strict keys and types; a structural problem is an ``offering_invalid`` error
    that aborts the document load (like every other strict parse error). Domain
    rules that must surface as droppable per-route diagnostics — unknown plans,
    an out-of-range quota weight, a non-selectable default, and more than one
    default per capability — are checked later in the validator, so the raw
    ``labels``/``plans``/``quota_weight`` are kept verbatim here.
    """
    if not isinstance(raw, Mapping):
        raise _err(
            "route",
            obj_id,
            DiagnosticCode.OFFERING_INVALID,
            f"{path} must be an object",
            path,
        )
    _reject_unknown_keys(raw, _OFFERING_KEYS, "route", obj_id, path)

    def _str_list(key: str) -> tuple[str, ...]:
        value = raw.get(key, [])
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            raise _err(
                "route",
                obj_id,
                DiagnosticCode.OFFERING_INVALID,
                f"{path}.{key} must be a list of strings",
                f"{path}.{key}",
            )
        return tuple(value)

    def _bool(key: str) -> bool:
        value = raw.get(key, False)
        if not isinstance(value, bool):
            raise _err(
                "route",
                obj_id,
                DiagnosticCode.OFFERING_INVALID,
                f"{path}.{key} must be a boolean",
                f"{path}.{key}",
            )
        return value

    def _opt_str(key: str) -> str | None:
        value = raw.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise _err(
                "route",
                obj_id,
                DiagnosticCode.OFFERING_INVALID,
                f"{path}.{key} must be a string",
                f"{path}.{key}",
            )
        return value

    quota_raw = raw.get("quota_weight", _OFFERING_QUOTA_WEIGHT_DEFAULT)
    if isinstance(quota_raw, bool) or not isinstance(quota_raw, (int, float)):
        raise _err(
            "route",
            obj_id,
            DiagnosticCode.OFFERING_INVALID,
            f"{path}.quota_weight must be a number",
            f"{path}.quota_weight",
        )

    return Offering(
        selectable=_bool("selectable"),
        display_name=_opt_str("display_name"),
        description=_opt_str("description"),
        labels=_str_list("labels"),
        plans=_str_list("plans"),
        quota_weight=float(quota_raw),
        default=_bool("default"),
    )


def _parse_route(raw: object, index: int) -> RouteRecord:
    path = f"routes[{index}]"
    value = _require_mapping(raw, "route", f"#{index}", path)
    obj_id = _str_field(value, "id", "route", f"#{index}", path, required=True) or f"#{index}"
    # Reject Tier 2/3 route options before unknown-key handling, so the
    # diagnostic names the tier gate rather than "unknown key".
    for option in _TIER2_ROUTE_OPTIONS:
        if option in value:
            raise _err(
                "route",
                obj_id,
                DiagnosticCode.OPTION_NOT_AVAILABLE,
                f"route option {option!r} is Tier 2 and not available",
                f"{path}.{option}",
            )
    _reject_unknown_keys(value, _ROUTE_KEYS, "route", obj_id, path)
    capability_raw = _str_field(value, "capability", "route", obj_id, path, required=True)
    assert capability_raw is not None
    if capability_raw in _LEGACY_CAPABILITY_ALIASES:
        raise _err(
            "route",
            obj_id,
            DiagnosticCode.CAPABILITY_LEGACY_ALIAS,
            f"legacy capability alias {capability_raw!r} is not allowed in the "
            "registry file; use the fine-grained id",
            f"{path}.capability",
        )
    try:
        capability = InferenceCapability(capability_raw)
    except ValueError as exc:
        raise _err(
            "route",
            obj_id,
            DiagnosticCode.CAPABILITY_UNKNOWN,
            f"unknown capability id {capability_raw!r}",
            f"{path}.capability",
        ) from exc
    strategy = _str_field(value, "strategy", "route", obj_id, path)
    if strategy is not None and strategy not in _STRATEGIES:
        raise _err(
            "route",
            obj_id,
            DiagnosticCode.STRATEGY_UNKNOWN,
            f"unknown strategy {strategy!r}",
            f"{path}.strategy",
        )
    compute = _str_field(value, "compute_requirement", "route", obj_id, path) or "any"
    if compute not in _COMPUTE_REQUIREMENTS:
        raise _err(
            "route",
            obj_id,
            DiagnosticCode.COMPUTE_REQUIREMENT_UNKNOWN,
            f"unknown compute_requirement {compute!r}",
            f"{path}.compute_requirement",
        )
    scope = _str_field(value, "scope", "route", obj_id, path) or "global"
    if scope != "global" and not scope.startswith("workspace:"):
        raise _err(
            "route",
            obj_id,
            DiagnosticCode.SCOPE_INVALID,
            f"scope must be 'global' or 'workspace:<id>', got {scope!r}",
            f"{path}.scope",
        )
    mapping_raw = value.get("mapping")
    if mapping_raw is not None and not isinstance(mapping_raw, Mapping):
        raise _err(
            "route",
            obj_id,
            DiagnosticCode.INVALID_FIELD,
            f"{path}.mapping must be an object",
            f"{path}.mapping",
        )
    label_aliases = value.get("label_aliases", {})
    if not isinstance(label_aliases, Mapping) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in label_aliases.items()
    ):
        raise _err(
            "route",
            obj_id,
            DiagnosticCode.INVALID_FIELD,
            f"{path}.label_aliases must be an object of string -> string",
            f"{path}.label_aliases",
        )
    params = value.get("params", {})
    if not isinstance(params, Mapping):
        raise _err(
            "route",
            obj_id,
            DiagnosticCode.INVALID_FIELD,
            f"{path}.params must be an object",
            f"{path}.params",
        )
    model_id = _str_field(value, "model_id", "route", obj_id, path, required=True)
    model_revision = _str_field(
        value, "model_revision", "route", obj_id, path, required=True
    )
    adapter_id = _str_field(value, "adapter_id", "route", obj_id, path, required=True)
    pricing = (
        _parse_pricing(value["pricing"], obj_id, f"{path}.pricing")
        if "pricing" in value and value["pricing"] is not None
        else PricingRecord()
    )
    budget = (
        _parse_budget(value["budget"], obj_id, f"{path}.budget")
        if "budget" in value and value["budget"] is not None
        else BudgetRecord()
    )
    limits = (
        _parse_limits(value["limits"], obj_id, f"{path}.limits")
        if "limits" in value and value["limits"] is not None
        else Limits()
    )
    input_transform = (
        _parse_input_transform(
            value["input_transform"], obj_id, f"{path}.input_transform"
        )
        if "input_transform" in value and value["input_transform"] is not None
        else None
    )
    offering = (
        _parse_offering(value["offering"], obj_id, f"{path}.offering")
        if "offering" in value and value["offering"] is not None
        else None
    )
    assert model_id is not None and model_revision is not None and adapter_id is not None
    return RouteRecord(
        id=obj_id,
        capability=capability,
        adapter_id=adapter_id,
        model_id=model_id,
        model_revision=model_revision,
        model_ref=_str_field(value, "model_ref", "route", obj_id, path),
        allow_provider_managed_revision=_bool_field(
            value, "allow_provider_managed_revision", "route", obj_id, path, False
        ),
        priority=_int_field(value, "priority", "route", obj_id, path) or 100,
        strategy=strategy,
        provider=_str_field(value, "provider", "route", obj_id, path) or adapter_id,
        pricing=pricing,
        explicitly_free=_bool_field(
            value, "explicitly_free", "route", obj_id, path, False
        ),
        budget=budget,
        timeout_s=_number_field(value, "timeout_s", "route", obj_id, path),
        compute_requirement=compute,
        requires_gpu=_bool_field(value, "requires_gpu", "route", obj_id, path, False),
        limits=limits,
        enabled=_bool_field(value, "enabled", "route", obj_id, path, True),
        scope=scope,
        embedding_space=_str_field(value, "embedding_space", "route", obj_id, path),
        params=dict(params),
        mapping=dict(mapping_raw) if mapping_raw is not None else None,
        upload_filename=_str_field(value, "upload_filename", "route", obj_id, path),
        label_aliases=dict(label_aliases),
        input_transform=input_transform,
        offering=offering,
        source="file",
    )


def load_registry_document(
    source: Path | str | Mapping,
) -> tuple[RegistryDocument | None, list[Diagnostic]]:
    """Parse a registry file or mapping strictly into a :class:`RegistryDocument`.

    ``source`` may be a :class:`~pathlib.Path`, a JSON string, or an already
    decoded mapping. Parsing is deterministic: records keep file order, and the
    first structural problem in a record aborts that whole document with a single
    diagnostic (returning ``(None, [diagnostic])``). No secret value is read.
    """
    payload: object
    if isinstance(source, Mapping):
        payload = source
    else:
        if isinstance(source, Path):
            try:
                text = source.read_text(encoding="utf-8")
            except OSError as exc:
                return None, [
                    _diag(
                        "document",
                        str(source),
                        "error",
                        DiagnosticCode.INVALID_JSON,
                        f"could not read registry file: {exc}",
                        "$",
                    )
                ]
        else:
            text = str(source)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            return None, [
                _diag(
                    "document",
                    "registry",
                    "error",
                    DiagnosticCode.INVALID_JSON,
                    f"registry is not valid JSON: {exc}",
                    "$",
                )
            ]

    if not isinstance(payload, Mapping):
        return None, [
            _diag(
                "document",
                "registry",
                "error",
                DiagnosticCode.INVALID_DOCUMENT,
                "registry document must be a JSON object",
                "$",
            )
        ]

    version = payload.get("version")
    if version != REGISTRY_FORMAT_VERSION:
        return None, [
            _diag(
                "document",
                "registry",
                "error",
                DiagnosticCode.UNSUPPORTED_VERSION,
                f"unsupported registry version {version!r}; expected "
                f"{REGISTRY_FORMAT_VERSION}",
                "$.version",
            )
        ]

    try:
        _reject_unknown_keys(payload, _TOP_LEVEL_KEYS, "document", "registry", "$")
        adapters = _parse_sequence(payload.get("adapters", []), "adapters", _parse_adapter)
        models = _parse_sequence(payload.get("models", []), "models", _parse_model)
        routes = _parse_sequence(payload.get("routes", []), "routes", _parse_route)
    except _LoadError as exc:
        return None, [exc.diagnostic]

    document = RegistryDocument(
        version=REGISTRY_FORMAT_VERSION,
        adapters=tuple(adapters),
        models=tuple(models),
        routes=tuple(routes),
    )
    return document, []


def _parse_sequence(raw: object, name: str, parser: Callable[[object, int], object]):
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise _err(
            "document",
            "registry",
            DiagnosticCode.INVALID_FIELD,
            f"$.{name} must be a JSON array",
            f"$.{name}",
        )
    return [parser(item, index) for index, item in enumerate(raw)]


# -- Fragment merge (plan Task 14) --------------------------------------------
def _split_registry_sources(spec: str) -> list[str]:
    """Split a comma-separated ``AI_REGISTRY_FILE`` into individual paths.

    Blank entries (from a trailing comma or whitespace) are dropped. Order is
    preserved so the merge is deterministic.
    """
    return [part.strip() for part in spec.split(",") if part.strip()]


def merge_registry_documents(
    documents: Sequence[RegistryDocument],
) -> tuple[RegistryDocument | None, list[Diagnostic]]:
    """Merge registry fragments into one document (plan Task 14).

    Operators compose a remote registry with the local fragments
    (``infra/ai-registry/cpu-local.json``/``gpu-local.json``) by listing them in
    ``AI_REGISTRY_FILE`` (comma-separated). Fragments are merged in order; an
    adapter, model or route id that appears in more than one fragment is a
    ``registry_fragment_collision`` error, so two fragments can never silently
    shadow each other. An empty list yields an empty version-1 document.
    """
    adapters: list[AdapterRecord] = []
    models: list[ModelRecord] = []
    routes: list[RouteRecord] = []
    adapter_ids: dict[str, int] = {}
    model_ids: dict[str, int] = {}
    route_ids: dict[str, int] = {}
    diagnostics: list[Diagnostic] = []

    def _merge(records, seen: dict[str, int], target: list, kind: str) -> None:
        for record in records:
            if record.id in seen:
                diagnostics.append(
                    _diag(
                        kind,
                        record.id,
                        "error",
                        DiagnosticCode.REGISTRY_FRAGMENT_COLLISION,
                        f"{kind} id {record.id!r} is defined in more than one "
                        "registry fragment",
                        f"$.{kind}s",
                    )
                )
                continue
            seen[record.id] = 1
            target.append(record)

    for document in documents:
        _merge(document.adapters, adapter_ids, adapters, "adapter")
        _merge(document.models, model_ids, models, "model")
        _merge(document.routes, route_ids, routes, "route")

    if diagnostics:
        return None, diagnostics

    merged = RegistryDocument(
        version=REGISTRY_FORMAT_VERSION,
        adapters=tuple(adapters),
        models=tuple(models),
        routes=tuple(routes),
    )
    return merged, []


def load_registry_sources(
    spec: Path | str | Mapping,
) -> tuple[RegistryDocument | None, list[Diagnostic]]:
    """Load one or more registry sources and merge them (plan Task 14).

    ``spec`` is either a single source accepted by :func:`load_registry_document`
    (a :class:`~pathlib.Path`, a JSON string or a decoded mapping), or a
    comma-separated string of file paths. A single source behaves exactly like
    :func:`load_registry_document`; multiple sources are each loaded and then
    merged with :func:`merge_registry_documents`. The first load error aborts
    with that diagnostic; a merge collision aborts with its diagnostic.
    """
    if isinstance(spec, (Path, Mapping)):
        return load_registry_document(spec)
    raw = str(spec)
    sources = _split_registry_sources(raw)
    if len(sources) <= 1:
        # A bare path (no comma) or a JSON string loads directly, so a single
        # inline JSON document still works for tests and dev.
        return load_registry_document(Path(sources[0]) if sources else raw)
    documents: list[RegistryDocument] = []
    for source in sources:
        document, diagnostics = load_registry_document(Path(source))
        if document is None:
            return None, diagnostics
        documents.append(document)
    return merge_registry_documents(documents)


# -- Locality -----------------------------------------------------------------
def is_self_hosted(adapter: AdapterRecord) -> bool:
    """True for a self-hosted/local route adapter (plan Section 2.6).

    Self-hosted = adapter type ``local`` or ``agent_pool``, or ``network:
    private``. Everything else is remote/hosted.
    """
    return adapter.type in _SELF_HOSTED_ADAPTER_TYPES or adapter.network == "private"


def route_locality(adapter: AdapterRecord) -> str:
    """Return ``"self_hosted"`` or ``"remote"`` for an adapter."""
    return "self_hosted" if is_self_hosted(adapter) else "remote"


def _ip_literal(host: str | None):
    if not host:
        return None
    candidate = host.strip("[]")
    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        return None


def _ip_is_private(address) -> bool:
    return bool(
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_unspecified
        or address.is_reserved
        or address.is_multicast
        or not address.is_global
    )


def _validate_base_url(
    adapter: AdapterRecord, path: str
) -> list[Diagnostic]:
    """Apply the Section 2.2 base_url rules; returns error diagnostics."""
    diagnostics: list[Diagnostic] = []
    # ``aws_bedrock`` derives its endpoint host from the configured region, so it
    # carries no base_url (plan Task 25); ``local``/``fake`` have no URL either.
    requires_base_url = adapter.type not in {"local", "fake", "aws_bedrock"}
    if not adapter.base_url:
        if requires_base_url:
            diagnostics.append(
                _diag(
                    "adapter",
                    adapter.id,
                    "error",
                    DiagnosticCode.BASE_URL_REQUIRED,
                    f"base_url is required for adapter type {adapter.type!r}",
                    f"{path}.base_url",
                )
            )
        return diagnostics

    try:
        parsed = urlsplit(adapter.base_url)
    except ValueError:
        diagnostics.append(
            _diag(
                "adapter",
                adapter.id,
                "error",
                DiagnosticCode.BASE_URL_INVALID,
                "base_url could not be parsed",
                f"{path}.base_url",
            )
        )
        return diagnostics

    scheme = parsed.scheme.lower()
    if scheme not in {"https", "http"} or not parsed.hostname:
        diagnostics.append(
            _diag(
                "adapter",
                adapter.id,
                "error",
                DiagnosticCode.BASE_URL_INVALID,
                "base_url must be an absolute http(s) URL with a host",
                f"{path}.base_url",
            )
        )
        return diagnostics
    if scheme == "http" and adapter.network != "private":
        diagnostics.append(
            _diag(
                "adapter",
                adapter.id,
                "error",
                DiagnosticCode.BASE_URL_SCHEME_INVALID,
                "http base_url is only allowed when network is private",
                f"{path}.base_url",
            )
        )
    if parsed.username or parsed.password:
        diagnostics.append(
            _diag(
                "adapter",
                adapter.id,
                "error",
                DiagnosticCode.BASE_URL_HAS_CREDENTIALS,
                "base_url must not contain userinfo (credentials)",
                f"{path}.base_url",
            )
        )
    if parsed.query or parsed.fragment:
        diagnostics.append(
            _diag(
                "adapter",
                adapter.id,
                "error",
                DiagnosticCode.BASE_URL_HAS_QUERY,
                "base_url must not contain a query string or fragment",
                f"{path}.base_url",
            )
        )
    address = _ip_literal(parsed.hostname)
    if address is not None:
        private = _ip_is_private(address)
        if adapter.network == "private" and not private:
            diagnostics.append(
                _diag(
                    "adapter",
                    adapter.id,
                    "error",
                    DiagnosticCode.BASE_URL_PRIVATE_IP_PUBLIC_NETWORK,
                    "a private-network adapter must not use a public IP literal",
                    f"{path}.base_url",
                )
            )
        if adapter.network == "public" and private:
            diagnostics.append(
                _diag(
                    "adapter",
                    adapter.id,
                    "error",
                    DiagnosticCode.BASE_URL_PUBLIC_IP_PRIVATE_NETWORK,
                    "a public adapter must not point at a private, loopback, "
                    "link-local or metadata IP literal",
                    f"{path}.base_url",
                )
            )
    return diagnostics


def _validate_secret_ref(
    adapter: AdapterRecord,
    secret_ref: str | None,
    app_env: str,
    path: str,
    field_name: str,
) -> list[Diagnostic]:
    """Validate a ``file:``/``bundle:`` secret ref; ``db:`` and ``env:`` rules."""
    if secret_ref is None:
        return []
    scheme, _, rest = secret_ref.partition(":")
    full_path = f"{path}.{field_name}"
    if not rest:
        return [
            _diag(
                "adapter",
                adapter.id,
                "error",
                DiagnosticCode.SECRET_REF_INVALID,
                "secret_ref must look like 'file:<name>' or 'bundle:<key>'",
                full_path,
            )
        ]
    if scheme in {"file", "bundle"}:
        return []
    if scheme == "db":
        return [
            _diag(
                "adapter",
                adapter.id,
                "error",
                DiagnosticCode.SECRET_REF_NOT_AVAILABLE,
                "db: secret refs are not available until Task 20",
                full_path,
            )
        ]
    if scheme == "env":
        if app_env == "production":
            return [
                _diag(
                    "adapter",
                    adapter.id,
                    "error",
                    DiagnosticCode.SECRET_REF_ENV_REJECTED,
                    "env: secret refs are rejected in production",
                    full_path,
                )
            ]
        return [
            _diag(
                "adapter",
                adapter.id,
                "warning",
                DiagnosticCode.SECRET_REF_ENV_WARNING,
                "env: secret refs are discouraged outside production",
                full_path,
            )
        ]
    return [
        _diag(
            "adapter",
            adapter.id,
            "error",
            DiagnosticCode.SECRET_REF_INVALID,
            f"unknown secret_ref scheme {scheme!r}",
            full_path,
        )
    ]


def validate_registry(
    document: RegistryDocument,
    *,
    app_env: str,
    gpu_workers_enabled: bool,
    block_noncommercial: bool,
    mapping_validator: Callable[[str, Mapping[str, object]], list[str]] | None = None,
    workspace_scoped_routes_enabled: bool = False,
) -> RegistryValidation:
    """Apply every Section 2.6 rule to a parsed document.

    An invalid route (any error diagnostic) is dropped from ``valid_route_ids``;
    its diagnostics stay in the result. If a required capability has no valid
    enabled route left, ``required_capability_missing`` is an error.
    ``mapping_validator`` is injected by Task 6 (``validate_mapping``); when
    ``None`` a mapping is only shape-checked (it must be an object, already done
    by the loader).
    """
    is_production = app_env == "production"
    diagnostics: list[Diagnostic] = []

    # Duplicate ids across each kind.
    adapters_by_id = _check_duplicates(document.adapters, "adapter", diagnostics)
    models_by_id = _check_duplicates(document.models, "model", diagnostics)
    routes_by_id = _check_duplicates(document.routes, "route", diagnostics)

    # Adapter-level diagnostics. An adapter with any error invalidates its routes.
    adapter_errors: dict[str, bool] = {}
    for index, adapter in enumerate(document.adapters):
        path = f"adapters[{index}]"
        adapter_diags = _validate_adapter(adapter, app_env, path)
        diagnostics.extend(adapter_diags)
        if any(d.is_error for d in adapter_diags):
            adapter_errors[adapter.id] = True

    valid_route_ids: list[str] = []
    dropped_route_ids: list[str] = []
    # Track valid enabled routes per capability for the required-capability rule
    # and the embedding-space cross-check.
    valid_enabled_by_capability: dict[InferenceCapability, list[RouteRecord]] = {}

    for index, route in enumerate(document.routes):
        path = f"routes[{index}]"
        route_diags: list[Diagnostic] = []
        adapter = adapters_by_id.get(route.adapter_id)
        if adapter is None:
            route_diags.append(
                _diag(
                    "route",
                    route.id,
                    "error",
                    DiagnosticCode.UNKNOWN_REFERENCE,
                    f"adapter_id {route.adapter_id!r} does not exist",
                    f"{path}.adapter_id",
                )
            )
        if route.model_ref is not None and route.model_ref not in models_by_id:
            route_diags.append(
                _diag(
                    "route",
                    route.id,
                    "error",
                    DiagnosticCode.UNKNOWN_REFERENCE,
                    f"model_ref {route.model_ref!r} does not exist",
                    f"{path}.model_ref",
                )
            )
        if adapter is not None:
            route_diags.extend(
                _validate_route_rules(
                    route,
                    adapter,
                    models_by_id.get(route.model_ref) if route.model_ref else None,
                    path,
                    app_env=app_env,
                    is_production=is_production,
                    block_noncommercial=block_noncommercial,
                    gpu_workers_enabled=gpu_workers_enabled,
                    workspace_scoped_routes_enabled=workspace_scoped_routes_enabled,
                    mapping_validator=mapping_validator,
                )
            )
        diagnostics.extend(route_diags)
        has_error = adapter_errors.get(route.adapter_id, False) or any(
            d.is_error for d in route_diags
        )
        if has_error:
            dropped_route_ids.append(route.id)
        else:
            valid_route_ids.append(route.id)
            if route.enabled:
                valid_enabled_by_capability.setdefault(route.capability, []).append(
                    route
                )

    # Required-capability rule (plan invariant 3). This is evaluated after the
    # per-route pass, so an invalid required route drops and still fails startup
    # via this diagnostic rather than hiding the route's own errors.
    for capability in sorted(REQUIRED_CAPABILITIES, key=lambda c: c.value):
        if not valid_enabled_by_capability.get(capability):
            diagnostics.append(
                _diag(
                    "capability",
                    capability.value,
                    "error",
                    DiagnosticCode.REQUIRED_CAPABILITY_MISSING,
                    f"required capability {capability.value!r} has no valid enabled "
                    "route",
                    "$.routes",
                )
            )

    # Embedding-space cross-check: text and image embedding routes used together
    # must declare the same embedding_space (plan Section 2.1/2.6).
    diagnostics.extend(
        _validate_embedding_spaces(valid_enabled_by_capability, routes_by_id)
    )

    # Offering default rules per capability (plan Section 2.11, Task 29). At most
    # one valid selectable route per capability may set ``default: true``; a
    # ``default: true`` on a non-selectable route is a warning. Evaluated across
    # the valid enabled routes so a dropped route never counts as a default.
    diagnostics.extend(_validate_offering_defaults(valid_enabled_by_capability))

    diagnostics.sort(key=lambda d: (0 if d.is_error else 1, d.object_id, d.code))
    return RegistryValidation(
        document=document,
        diagnostics=tuple(diagnostics),
        valid_route_ids=tuple(valid_route_ids),
        dropped_route_ids=tuple(dropped_route_ids),
    )


def _check_duplicates(records, kind: str, diagnostics: list[Diagnostic]) -> dict:
    by_id: dict[str, object] = {}
    for index, record in enumerate(records):
        if record.id in by_id:
            diagnostics.append(
                _diag(
                    kind,
                    record.id,
                    "error",
                    DiagnosticCode.DUPLICATE_ID,
                    f"duplicate {kind} id {record.id!r}",
                    f"{kind}s[{index}].id",
                )
            )
        else:
            by_id[record.id] = record
    return by_id


def _validate_adapter(
    adapter: AdapterRecord, app_env: str, path: str
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    diagnostics.extend(_validate_base_url(adapter, path))
    auth = adapter.auth
    diagnostics.extend(
        _validate_secret_ref(adapter, auth.secret_ref, app_env, f"{path}.auth", "secret_ref")
    )
    diagnostics.extend(
        _validate_secret_ref(
            adapter, auth.client_secret_ref, app_env, f"{path}.auth", "client_secret_ref"
        )
    )
    if auth.type == "custom_header" and not auth.header_name:
        diagnostics.append(
            _diag(
                "adapter",
                adapter.id,
                "error",
                DiagnosticCode.AUTH_MISSING_HEADER_NAME,
                "custom_header auth requires header_name",
                f"{path}.auth.header_name",
            )
        )
    if auth.type == "oauth2_client_credentials" and not (
        auth.token_url and auth.client_id and auth.client_secret_ref
    ):
        diagnostics.append(
            _diag(
                "adapter",
                adapter.id,
                "error",
                DiagnosticCode.AUTH_MISSING_OAUTH_FIELDS,
                "oauth2_client_credentials auth requires token_url, client_id and "
                "client_secret_ref",
                f"{path}.auth",
            )
        )
    diagnostics.extend(_validate_adapter_config(adapter, path))
    return diagnostics


def _validate_adapter_config(
    adapter: AdapterRecord, path: str
) -> list[Diagnostic]:
    """Validate a typed adapter ``config`` block (plan Tasks 24/25).

    Delegates the field rules to the adapter module's own ``validate_*`` function
    so the registry mirrors the two config dataclasses exactly, and never has to
    restate them. ``huggingface_endpoint`` additionally requires bearer auth with
    a secret ref (its endpoints only accept a bearer token); ``aws_bedrock`` is
    the IAM path and takes no auth secret here (the route-level rule allows
    ``auth: none``).
    """
    diagnostics: list[Diagnostic] = []
    config = adapter.config

    def _config_error(message: str) -> None:
        diagnostics.append(
            _diag(
                "adapter",
                adapter.id,
                "error",
                DiagnosticCode.ADAPTER_CONFIG_INVALID,
                f"config is invalid: {message}",
                f"{path}.config",
            )
        )

    if adapter.type == "huggingface_endpoint":
        from app.services.huggingface_endpoint_adapter import (
            validate_huggingface_config,
        )

        timeout = config.get("timeout_s", 60.0)
        errors = validate_huggingface_config(
            base_url=adapter.base_url or "",
            mode=config.get("mode"),  # type: ignore[arg-type]
            pipeline_task=config.get("pipeline_task"),  # type: ignore[arg-type]
            timeout_s=timeout if isinstance(timeout, (int, float)) else -1.0,
        )
        for problem in errors:
            _config_error(problem)
        allowed_hf = {"mode", "pipeline_task", "timeout_s", "max_response_bytes"}
        for key in config:
            if key not in allowed_hf:
                _config_error(f"unknown key {key!r}")
        # Bearer secret ref only (plan Task 24). The route-level remote-auth rule
        # requires an auth secret for a remote route; here we pin the *type* to
        # bearer so a hosted endpoint cannot be configured with another scheme.
        if adapter.network != "private" and adapter.auth.type != "bearer":
            diagnostics.append(
                _diag(
                    "adapter",
                    adapter.id,
                    "error",
                    DiagnosticCode.ADAPTER_CONFIG_INVALID,
                    "huggingface_endpoint auth must be bearer with a secret ref",
                    f"{path}.auth.type",
                )
            )
    elif adapter.type == "aws_bedrock":
        from app.services.aws_bedrock_adapter import validate_bedrock_config

        timeout = config.get("timeout_s", 60.0)
        errors = validate_bedrock_config(
            region=config.get("region"),  # type: ignore[arg-type]
            api=config.get("api"),  # type: ignore[arg-type]
            embedding_family=config.get("embedding_family"),  # type: ignore[arg-type]
            timeout_s=timeout if isinstance(timeout, (int, float)) else -1.0,
        )
        for problem in errors:
            _config_error(problem)
        # Reject unknown config keys so the block mirrors ``BedrockConfig``.
        allowed = {"region", "api", "embedding_family", "timeout_s"}
        for key in config:
            if key not in allowed:
                _config_error(f"unknown key {key!r}")
    return diagnostics


def _route_pricing_is_zero(route: RouteRecord) -> bool:
    pricing = route.pricing
    if pricing.status != "priced":
        return False
    amounts = [
        pricing.input_per_million_usd,
        pricing.output_per_million_usd,
        pricing.per_request_usd,
        pricing.per_unit_usd,
    ]
    return all((amount or 0.0) == 0.0 for amount in amounts)


def _validate_route_rules(
    route: RouteRecord,
    adapter: AdapterRecord,
    model: ModelRecord | None,
    path: str,
    *,
    app_env: str,
    is_production: bool,
    block_noncommercial: bool,
    gpu_workers_enabled: bool,
    workspace_scoped_routes_enabled: bool,
    mapping_validator: Callable[[str, Mapping[str, object]], list[str]] | None,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    self_hosted = is_self_hosted(adapter)

    def add(severity: str, code: str, message: str, field_path: str = path) -> None:
        diagnostics.append(
            _diag("route", route.id, severity, code, message, field_path)
        )

    # Scope gate (workspace routes rejected while the flag is off).
    if route.scope.startswith("workspace:") and not workspace_scoped_routes_enabled:
        add(
            "error",
            DiagnosticCode.SCOPE_WORKSPACE_DISABLED,
            "workspace-scoped routes are rejected while AI_WORKSPACE_SCOPED_ROUTES "
            "is off",
            f"{path}.scope",
        )

    # Capability/adapter-type support matrix.
    supported = _SUPPORT_MATRIX.get(adapter.type, frozenset())
    if route.capability not in supported:
        add(
            "error",
            DiagnosticCode.CAPABILITY_ADAPTER_UNSUPPORTED,
            f"adapter type {adapter.type!r} does not support "
            f"{route.capability.value}",
            f"{path}.capability",
        )

    # Revision policy by locality.
    revision = route.model_revision.strip().lower()
    if revision == "provider-managed":
        if self_hosted:
            add(
                "error",
                DiagnosticCode.REVISION_PROVIDER_MANAGED_SELF_HOSTED,
                "provider-managed revisions are not allowed on self-hosted routes",
                f"{path}.model_revision",
            )
        elif not route.allow_provider_managed_revision:
            add(
                "error",
                DiagnosticCode.REVISION_PROVIDER_MANAGED_NOT_ALLOWED,
                "provider-managed revision requires "
                "allow_provider_managed_revision: true",
                f"{path}.model_revision",
            )
        else:
            add(
                "warning",
                DiagnosticCode.REVISION_PROVIDER_MANAGED,
                "route uses a provider-managed revision; pin it when possible",
                f"{path}.model_revision",
            )
    elif revision == route.model_id.strip().lower():
        add(
            "error",
            DiagnosticCode.REVISION_EQUALS_MODEL_ID,
            "model_revision must not equal the model id",
            f"{path}.model_revision",
        )
    elif "latest" in revision or "floating" in revision:
        add(
            "error",
            DiagnosticCode.REVISION_FLOATING,
            "model_revision must be an immutable pin, not 'latest' or a floating "
            "alias",
            f"{path}.model_revision",
        )

    # Licence and commercial use.
    licence = model.licence if model is not None else None
    commercial = model.commercial_use if model is not None else None
    if self_hosted:
        if not licence:
            add(
                "error",
                DiagnosticCode.LICENCE_REQUIRED,
                "self-hosted routes require a model licence",
                f"{path}.model_ref",
            )
        elif commercial is False:
            if block_noncommercial:
                add(
                    "error",
                    DiagnosticCode.LICENCE_NONCOMMERCIAL,
                    "non-commercial licence is blocked by AI_BLOCK_NONCOMMERCIAL",
                    f"{path}.model_ref",
                )
            else:
                add(
                    "warning",
                    DiagnosticCode.LICENCE_NONCOMMERCIAL,
                    "model licence is non-commercial",
                    f"{path}.model_ref",
                )
    else:
        if not licence:
            add(
                "warning",
                DiagnosticCode.LICENCE_MISSING,
                "remote route has no licence metadata",
                f"{path}.model_ref",
            )
        elif commercial is False:
            if block_noncommercial:
                add(
                    "error",
                    DiagnosticCode.LICENCE_NONCOMMERCIAL,
                    "non-commercial licence is blocked by AI_BLOCK_NONCOMMERCIAL",
                    f"{path}.model_ref",
                )
            else:
                add(
                    "warning",
                    DiagnosticCode.LICENCE_NONCOMMERCIAL,
                    "model licence is non-commercial",
                    f"{path}.model_ref",
                )

    # Data-processor metadata (remote only).
    if not self_hosted and adapter.data_processor is None:
        if is_production:
            add(
                "error",
                DiagnosticCode.DATA_PROCESSOR_MISSING,
                "remote routes require data_processor metadata in production",
                path,
            )
        else:
            add(
                "warning",
                DiagnosticCode.DATA_PROCESSOR_MISSING,
                "remote route is missing data_processor metadata",
                path,
            )

    # Auth.
    if self_hosted:
        pass  # auth: none is allowed on self-hosted routes.
    elif adapter.type in _IAM_CREDENTIAL_ADAPTER_TYPES:
        # ``aws_bedrock`` authenticates from the IAM credential chain
        # (env/ECS/IMDS) and SigV4, so a remote Bedrock route stores no AI secret
        # ref and ``auth: none`` is the expected configuration (plan Task 25 "the
        # IAM path stores no static cloud key as an AI secret").
        pass
    else:
        if adapter.auth.type == "none":
            add(
                "error",
                DiagnosticCode.AUTH_NONE_REMOTE,
                "remote routes require authentication; auth 'none' is only allowed "
                "for self-hosted routes",
                f"{path}.adapter_id",
            )
        elif not (adapter.auth.secret_ref or adapter.auth.client_secret_ref):
            add(
                "error",
                DiagnosticCode.AUTH_REQUIRED,
                "remote routes require an auth secret reference",
                f"{path}.adapter_id",
            )

    # Pricing.
    if route.pricing.status == "unknown":
        if not route.budget.hard_cap.has_positive_cap:
            add(
                "error",
                DiagnosticCode.PRICING_UNKNOWN_NEEDS_HARD_CAP,
                "unknown pricing requires a hard cap greater than 0",
                f"{path}.budget.hard_cap",
            )
    elif route.pricing.status == "priced" and _route_pricing_is_zero(route):
        if not route.explicitly_free:
            add(
                "error",
                DiagnosticCode.PRICING_ZERO_NOT_FREE,
                "a zero price requires explicitly_free: true",
                f"{path}.pricing",
            )

    # Free planning route on a remote adapter in production is an error.
    is_free = route.explicitly_free or (
        route.pricing.status == "free"
    )
    if (
        route.capability == InferenceCapability.PLANNING_CHAT
        and not self_hosted
        and is_free
        and is_production
    ):
        add(
            "error",
            DiagnosticCode.FREE_PLANNING_REMOTE,
            "a free planning route on a remote adapter is not allowed in production",
            path,
        )

    # Fake adapter in production.
    if adapter.type == "fake" and is_production:
        add(
            "error",
            DiagnosticCode.FAKE_ADAPTER_IN_PRODUCTION,
            "fake adapters are not allowed in production",
            f"{path}.adapter_id",
        )

    # requires_gpu routes are dropped (warning) when GPU workers are disabled.
    if route.requires_gpu and not gpu_workers_enabled:
        add(
            "warning",
            DiagnosticCode.ROUTE_REQUIRES_GPU,
            "route requires a GPU but GPU workers are disabled; route dropped",
            path,
        )

    # Mapping requirement by adapter type.
    needs_mapping = adapter.type in _MAPPING_ADAPTER_TYPES
    if needs_mapping and route.mapping is None:
        add(
            "error",
            DiagnosticCode.MAPPING_REQUIRED,
            f"adapter type {adapter.type!r} requires a mapping",
            f"{path}.mapping",
        )
    elif not needs_mapping and route.mapping is not None:
        add(
            "error",
            DiagnosticCode.MAPPING_NOT_ALLOWED,
            f"a mapping is not allowed on adapter type {adapter.type!r}",
            f"{path}.mapping",
        )
    elif needs_mapping and route.mapping is not None and mapping_validator is not None:
        try:
            problems = mapping_validator(adapter.type, route.mapping)
        except Exception as exc:  # noqa: BLE001 - surface as a diagnostic
            problems = [f"mapping validator failed: {exc}"]
        for problem in problems:
            add(
                "error",
                DiagnosticCode.MAPPING_INVALID,
                f"mapping is invalid: {problem}",
                f"{path}.mapping",
            )

    # Offering metadata (plan Section 2.11, Task 29). Per-route value rules; the
    # per-capability default rules are applied in ``validate_registry``.
    offering = route.offering
    if offering is not None:
        bad_labels = [label for label in offering.labels if label not in _OFFERING_LABELS]
        if bad_labels:
            add(
                "error",
                DiagnosticCode.OFFERING_INVALID,
                f"offering labels {bad_labels!r} are not in the closed set "
                f"{sorted(_OFFERING_LABELS)}",
                f"{path}.offering.labels",
            )
        bad_plans = [plan for plan in offering.plans if plan not in _OFFERING_PLAN_KEYS]
        if bad_plans:
            add(
                "error",
                DiagnosticCode.OFFERING_PLAN_UNKNOWN,
                f"offering plans {bad_plans!r} are not known billing plans "
                f"{sorted(_OFFERING_PLAN_KEYS)}",
                f"{path}.offering.plans",
            )
        if not (
            _OFFERING_QUOTA_WEIGHT_MIN
            <= offering.quota_weight
            <= _OFFERING_QUOTA_WEIGHT_MAX
        ):
            add(
                "error",
                DiagnosticCode.OFFERING_QUOTA_WEIGHT_OUT_OF_RANGE,
                f"offering quota_weight {offering.quota_weight} must be between "
                f"{_OFFERING_QUOTA_WEIGHT_MIN} and {_OFFERING_QUOTA_WEIGHT_MAX}",
                f"{path}.offering.quota_weight",
            )

    return diagnostics


def _validate_offering_defaults(
    valid_enabled_by_capability: Mapping[InferenceCapability, list[RouteRecord]],
) -> list[Diagnostic]:
    """Per-capability offering default rules (plan Section 2.11, Task 29).

    At most one valid enabled selectable route per capability may set
    ``default: true`` (``offering_multiple_defaults`` error). A route that sets
    ``default: true`` without ``selectable: true`` cannot be the operator
    default users see, so it earns an ``offering_default_not_selectable``
    warning; the route still serves as a non-visible default/fallback.
    """
    diagnostics: list[Diagnostic] = []
    for capability, routes in valid_enabled_by_capability.items():
        selectable_defaults: list[RouteRecord] = []
        for route in routes:
            offering = route.offering
            if offering is None or not offering.default:
                continue
            if offering.selectable:
                selectable_defaults.append(route)
            else:
                diagnostics.append(
                    _diag(
                        "route",
                        route.id,
                        "warning",
                        DiagnosticCode.OFFERING_DEFAULT_NOT_SELECTABLE,
                        "offering sets default: true but not selectable: true; it "
                        "cannot be the user-visible operator default",
                        f"$.routes[{route.id}].offering.default",
                    )
                )
        if len(selectable_defaults) > 1:
            for route in selectable_defaults:
                diagnostics.append(
                    _diag(
                        "route",
                        route.id,
                        "error",
                        DiagnosticCode.OFFERING_MULTIPLE_DEFAULTS,
                        "more than one selectable route sets default: true for "
                        f"capability {capability.value!r}",
                        f"$.routes[{route.id}].offering.default",
                    )
                )
    diagnostics.sort(key=lambda d: (0 if d.is_error else 1, d.object_id, d.code))
    return diagnostics


def _validate_embedding_spaces(
    valid_enabled_by_capability: Mapping[InferenceCapability, list[RouteRecord]],
    routes_by_id: Mapping[str, RouteRecord],
) -> list[Diagnostic]:
    text_routes = valid_enabled_by_capability.get(InferenceCapability.EMBEDDINGS_TEXT, [])
    image_routes = valid_enabled_by_capability.get(
        InferenceCapability.EMBEDDINGS_IMAGE, []
    )
    if not text_routes or not image_routes:
        return []
    text_spaces = {r.embedding_space for r in text_routes}
    image_spaces = {r.embedding_space for r in image_routes}
    if text_spaces & image_spaces:
        return []
    # Report against the highest-priority (lowest number) image route for a
    # deterministic object id.
    offending = min(image_routes, key=lambda r: (r.priority, r.id))
    return [
        _diag(
            "route",
            offending.id,
            "error",
            DiagnosticCode.EMBEDDING_SPACE_MISMATCH,
            "text and image embedding routes used together must declare the same "
            "embedding_space",
            f"routes.{offending.id}.embedding_space",
        )
    ]


def to_model_route(route: RouteRecord, adapter: AdapterRecord) -> ModelRoute:
    """Build the gateway :class:`ModelRoute` for a registry route + adapter.

    - coarse capability family via ``coarse_family_for_capability``;
    - a single legacy ``ModelOperation`` via ``model_operation_for_capability``;
    - costs from ``pricing`` with today's unit convention (per-unit price ->
      ``input_cost_per_million_usd = per_unit_usd * 1e6``; per-request ->
      ``fixed_cost_usd``; token prices map directly);
    - ``endpoint_host`` from the adapter base_url (https host only);
    - ``requires_live_effects_gate`` True for remote routes, matching
      ``model_gateway_factory`` today;
    - ``budget_feature`` from the route budget;
    - ``max_output_units`` from limits when present.
    """
    pricing = route.pricing
    input_cost = pricing.input_per_million_usd or 0.0
    output_cost = pricing.output_per_million_usd or 0.0
    fixed_cost = pricing.per_request_usd or 0.0
    if pricing.per_unit_usd:
        # per-unit price maps onto the per-million input cost dimension.
        input_cost = pricing.per_unit_usd * 1e6
    explicitly_free = route.explicitly_free or pricing.status == "free"
    remote = not is_self_hosted(adapter)
    endpoint_host = https_host_from_url(adapter.base_url) if remote else None
    operation = model_operation_for_capability(route.capability)
    max_output_units = (
        route.limits.max_output_units
        if route.limits.max_output_units is not None
        else 4096
    )
    minimum_units = max(
        pricing.minimum_billable_units, route.pricing.minimum_billable_units
    )
    # An async-submit route is route-affine (plan invariant 6, Task 16A): any
    # ``async_job`` adapter, or a ``video.generation.submit`` route.
    async_submit = adapter.type == "async_job" or route.capability.value == (
        "video.generation.submit"
    )
    return ModelRoute(
        route_id=route.id,
        capability=coarse_family_for_capability(route.capability),
        adapter=adapter.type,
        provider=route.provider or adapter.id,
        model=route.model_id,
        model_revision=route.model_revision,
        operations=(operation,),
        priority=route.priority,
        input_cost_per_million_usd=0.0 if explicitly_free else input_cost,
        output_cost_per_million_usd=0.0 if explicitly_free else output_cost,
        fixed_cost_usd=0.0 if explicitly_free else fixed_cost,
        explicitly_free=explicitly_free,
        max_output_units=max_output_units,
        enabled=route.enabled,
        async_submit=async_submit,
        endpoint_host=endpoint_host,
        requires_live_effects_gate=remote,
        budget_feature=route.budget.feature,
        allow_provider_managed_revision=route.allow_provider_managed_revision,
        pricing_status=pricing.status,
        hard_cap_max_requests=route.budget.hard_cap.max_requests_per_month,
        hard_cap_max_units=route.budget.hard_cap.max_units_per_month,
        minimum_billable_units=minimum_units,
        timeout_s=route.timeout_s if route.timeout_s is not None else adapter.timeout_s,
        max_batch=route.limits.max_batch,
        max_request_bytes=route.limits.max_request_bytes,
        requires_gpu=route.requires_gpu,
        capability_id=route.capability.value,
        selectable=route.offering.selectable if route.offering is not None else False,
        quota_weight=(
            route.offering.quota_weight
            if route.offering is not None
            else _OFFERING_QUOTA_WEIGHT_DEFAULT
        ),
    )
