"""The Tier 1 mapping-template grammar engine (plan Section 2.10).

This module is pure and I/O-free. It is the single implementation of the frozen
Section 2.10 grammar slice, shared by the ``generic_http`` adapter (Task 15) and,
in a later stage, the ``async_job`` adapter (Task 16B, which will *add*
``async_job`` validation rules and ``ASYNC_JOB_*`` constants here without
restructuring the validator).

Two concerns live here:

* **Evaluation** (:func:`evaluate_value`): turn a value form — a JSON literal,
  ``$ref``, ``$fn`` or ``$map`` — into a concrete Python value against a
  :class:`TemplateContext` of roots (``input``, ``params``, ``vars``,
  ``response``, ``item``). Request-path placeholders are rendered by
  :func:`render_path`.
* **Validation** (:func:`validate_mapping`): reject any key, function or enum
  value that is not in the frozen Section 2.10 inventory, *before* a route is
  activated. The accepted key sets, function names and enum values are exported
  as module constants and pinned by ``test_inference_template_grammar_freeze``.

Nothing in this module reads the environment, secrets, the filesystem or the
network. Auth and transport are the adapter's job (Section 2.10, "Not allowed").
"""

from __future__ import annotations

import re
import uuid as _uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

# ---------------------------------------------------------------------------
# Frozen Section 2.10 inventory (exported constants).
#
# These are the single source of truth the grammar-freeze test pins against a
# literal copy of Section 2.10. Changing either side requires a plan revision.
# ---------------------------------------------------------------------------

# Value forms (Section 2.10 "Value forms", 6 elements).
VALUE_FORMS: tuple[str, ...] = (
    "json_literal",
    "ref",
    "fn",
    "map",
    "path_placeholder",
    "vars_block",
)

# $ref roots. Five "real" roots plus ``item`` (the current $map / fields element).
REF_ROOTS: tuple[str, ...] = ("input", "params", "vars", "response", "item")

# $ref path segment kinds.
REF_SEGMENTS: tuple[str, ...] = ("key", "[n]", "[*]", "{vars.<name>}")

# Allowed functions (the complete list, 9 elements).
FUNCTIONS: tuple[str, ...] = (
    "base64",
    "data_url",
    "uuid",
    "concat",
    "to_int",
    "to_float",
    "to_ms",
    "clamp01",
    "default",
)

# ``generic_http`` top-level mapping keys.
GENERIC_HTTP_KEYS: tuple[str, ...] = (
    "request",
    "items",
    "aligned_to_input",
    "fields",
    "box_format",
    "item_error",
    "item_error_codes",
    "error_map",
    "usage",
)

# ``request`` sub-keys.
GENERIC_HTTP_REQUEST_KEYS: tuple[str, ...] = ("method", "path", "body")

# The capability output field keys inside ``fields``.
GENERIC_HTTP_FIELD_KEYS: tuple[str, ...] = ("detections", "label", "score", "box")

# Box formats accepted in Tier 1 (Section 2.10). xyxy_norm/xyxy_px are Tier 3.
BOX_FORMATS: tuple[str, ...] = ("vertices_norm", "xywh_norm")

# ``item_error_codes`` right-hand values.
ITEM_ERROR_CLASSES: tuple[str, ...] = ("rejected", "unavailable")

# ``error_map`` right-hand values.
ERROR_MAP_CLASSES: tuple[str, ...] = (
    "rejected",
    "unavailable",
    "auth",
    "quota",
    "malformed",
)

# ``usage`` modes.
USAGE_MODES: tuple[str, ...] = ("count_items", "count_ok_items")

# Route options the Tier 1 slice reads (data, not grammar).
ROUTE_OPTIONS: tuple[str, ...] = (
    "input_transform.audio",
    "upload_filename",
    "label_aliases",
)

# Image MIME types ``data_url`` may sniff (Section 2.10: png/jpeg/webp/gif).
IMAGE_MIME_BY_FORMAT: Mapping[str, str] = {
    "png": "image/png",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
}

# ---------------------------------------------------------------------------
# Tier 3 extensions (plan Section 2.10 "Tier 3 extensions (Task 28)", and the
# freeze-table rows the lead adds there). These are the EXACT five extensions —
# nothing else is accepted:
#
#   1. Multipart request bodies: a ``{"$multipart": {...}}`` value form, whose
#      field values are value forms or ``{"$file": {...}}`` file parts.
#   2. Additional box formats ``xyxy_norm`` and ``xyxy_px`` (``xyxy_px`` needs
#      image dimensions, read from the request images by stdlib header parsing
#      in the adapter/normalizer — code, not grammar).
#   3. ``usage`` taken from a response path: usage mode ``response_path`` plus a
#      ``usage_path`` ``$ref`` the adapter reads from the response.
#   4. Non-blocking async mode: a top-level async_job ``mode`` key with values
#      ``blocking`` / ``non_blocking`` (the delayed-poll engine runs the latter).
#   5. Typed ``generic_http`` mappings for synchronous transcription (word
#      timestamps), captions and text/image embeddings on non-OpenAI-compatible
#      APIs: an ``output`` discriminator with a fixed typed field set per kind.
#
# Each is a single frozen grammar element. The grammar-freeze test pins the
# Tier 3 constants below against a literal copy of the Section 2.10 Tier 3 rows.
# ---------------------------------------------------------------------------

# 1. Multipart: the value-form group name (one new value form) and the part
#    keys. A ``$file`` part names an ``input`` byte source and an optional
#    filename/content_type; a plain part value is any value form.
TIER3_VALUE_FORMS: tuple[str, ...] = ("multipart",)
MULTIPART_PART_KEYS: tuple[str, ...] = ("value", "file", "filename", "content_type")

# 2. Tier 3 box formats (added to the accepted set; the Tier 1 BOX_FORMATS
#    constant stays frozen on its own row).
TIER3_BOX_FORMATS: tuple[str, ...] = ("xyxy_norm", "xyxy_px")

# 3. Tier 3 usage mode: a value read from a response path (``usage_path``).
TIER3_USAGE_MODES: tuple[str, ...] = ("response_path",)

# 4. Non-blocking async mode: the top-level async_job ``mode`` key and its enum.
ASYNC_JOB_MODE_KEY: str = "mode"
ASYNC_JOB_MODES: tuple[str, ...] = ("blocking", "non_blocking")

# 5. Typed generic_http output kinds and the fixed field set each one reads.
#    ``object_detection`` is the Tier 1 default (``detections``/``label``/
#    ``score``/``box``); the four Tier 3 kinds add their own fixed field sets.
GENERIC_HTTP_OUTPUT_KINDS: tuple[str, ...] = (
    "object_detection",
    "transcription",
    "caption",
    "text_embeddings",
    "image_embeddings",
)
# Transcription output fields (synchronous, with word timestamps). ``words`` is
# the per-word list; ``word``/``start``/``end``/``confidence`` are read relative
# to each word element; ``text``/``language`` are top-level transcript fields.
TRANSCRIPTION_FIELD_KEYS: tuple[str, ...] = (
    "text",
    "language",
    "words",
    "word",
    "start",
    "end",
    "confidence",
)
# Caption output fields (image-to-text). A single ``caption`` string field.
CAPTION_FIELD_KEYS: tuple[str, ...] = ("caption",)
# Embeddings output fields (text and image). ``vectors`` locates the per-input
# vector list; ``vector`` reads one vector relative to each element.
EMBEDDINGS_FIELD_KEYS: tuple[str, ...] = ("vectors", "vector")

# Units in which ``start``/``end`` transcription timestamps arrive, so the
# adapter converts to Motionly milliseconds without a grammar arithmetic op.
TRANSCRIPTION_TIME_UNITS: tuple[str, ...] = ("seconds", "milliseconds")

# Tier 3 ``generic_http`` top-level keys: the ``output`` discriminator and the
# ``usage_path`` read (used with ``usage: response_path``). The Tier 1
# GENERIC_HTTP_KEYS constant stays frozen on its own row.
TIER3_GENERIC_HTTP_KEYS: tuple[str, ...] = ("output", "usage_path", "time_unit")

# ---------------------------------------------------------------------------
# Accepted sets (Tier 1 + Tier 3), used by the validator. The Tier 1 and Tier 3
# constants above stay separate so the grammar-freeze test pins each tier's rows
# independently; the validator checks membership against the union.
# ---------------------------------------------------------------------------
ACCEPTED_BOX_FORMATS: frozenset[str] = frozenset(BOX_FORMATS) | frozenset(TIER3_BOX_FORMATS)
ACCEPTED_USAGE_MODES: frozenset[str] = frozenset(USAGE_MODES) | frozenset(TIER3_USAGE_MODES)
ACCEPTED_GENERIC_HTTP_KEYS: frozenset[str] = frozenset(GENERIC_HTTP_KEYS) | frozenset(
    TIER3_GENERIC_HTTP_KEYS
)
# The fixed field-key set accepted inside ``fields`` for each output kind.
_FIELD_KEYS_BY_OUTPUT: Mapping[str, frozenset[str]] = {
    "object_detection": frozenset(GENERIC_HTTP_FIELD_KEYS),
    "transcription": frozenset(TRANSCRIPTION_FIELD_KEYS),
    "caption": frozenset(CAPTION_FIELD_KEYS),
    "text_embeddings": frozenset(EMBEDDINGS_FIELD_KEYS),
    "image_embeddings": frozenset(EMBEDDINGS_FIELD_KEYS),
}

# ---------------------------------------------------------------------------
# ``async_job`` inventory (Section 2.10 "async_job keys", count 6).
#
# The six top-level groups are ``vars`` (the value-form initializer, counted
# among the value forms), ``steps``, ``commit``, ``poll``, ``result``,
# ``artifacts`` and ``cleanup``. Section 2.10's freeze table counts the
# *grammar* groups as 6: steps, commit, poll, result, artifacts, cleanup. The
# ``vars`` block is a value form (already counted there), not an async_job key.
# ---------------------------------------------------------------------------

# Top-level async_job mapping keys. ``vars`` is accepted (it is the value-form
# initializer) but is not one of the six frozen async_job grammar groups.
ASYNC_JOB_KEYS: tuple[str, ...] = (
    "vars",
    "steps",
    "poll",
    "result",
    "artifacts",
    "cleanup",
)

# The six frozen async_job grammar groups (Section 2.10 freeze-table "Elements",
# count 6). ``commit`` is a per-step flag, so it is a group here but a step key
# below.
ASYNC_JOB_GROUPS: tuple[str, ...] = (
    "steps",
    "commit",
    "poll",
    "result",
    "artifacts",
    "cleanup",
)

# Step sub-keys. ``path`` and ``url`` are mutually exclusive (``url`` only with
# ``auth: none``); ``commit`` is the billable-boundary flag.
ASYNC_JOB_STEP_KEYS: tuple[str, ...] = (
    "method",
    "path",
    "url",
    "body",
    "capture",
    "require",
    "auth",
    "commit",
)

# Step/artifact ``auth`` values.
ASYNC_JOB_AUTH_MODES: tuple[str, ...] = ("adapter", "none")

# ``require`` right-hand values (a captured var must satisfy this predicate).
ASYNC_JOB_REQUIRE_PREDICATES: tuple[str, ...] = ("https",)

# ``poll`` sub-keys.
ASYNC_JOB_POLL_KEYS: tuple[str, ...] = (
    "method",
    "path",
    "body",
    "status",
    "status_map",
    "tolerate_retryable",
    "interval_s",
    "backoff",
    "max_wait_s",
)

# ``backoff`` sub-keys.
ASYNC_JOB_BACKOFF_KEYS: tuple[str, ...] = ("factor", "max_interval_s")

# ``status_map`` entry sub-keys and the ``state`` enum.
ASYNC_JOB_STATUS_FIELDS: tuple[str, ...] = ("state", "billable", "fallback")
ASYNC_JOB_STATUS_STATES: tuple[str, ...] = (
    "running",
    "succeeded",
    "failed",
    "retryable_failed",
)

# ``result`` sub-keys (a mapping, as for generic_http: items + fields).
ASYNC_JOB_RESULT_KEYS: tuple[str, ...] = ("items", "fields")

# ``artifacts`` sub-keys.
ASYNC_JOB_ARTIFACT_KEYS: tuple[str, ...] = (
    "items",
    "name",
    "url",
    "name_map",
    "required",
    "max_bytes",
    "allowed_hosts",
    "force_https",
    "auth",
)

# ``cleanup`` request sub-keys (a bare list of requests).
ASYNC_JOB_CLEANUP_KEYS: tuple[str, ...] = ("method", "path", "body")

# Accepted top-level async_job keys = the frozen Tier 1 groups plus the Tier 3
# non-blocking ``mode`` discriminator. The Tier 1 ASYNC_JOB_KEYS constant stays
# frozen on its own row.
ACCEPTED_ASYNC_JOB_KEYS: frozenset[str] = frozenset(ASYNC_JOB_KEYS) | {ASYNC_JOB_MODE_KEY}

# HTTP methods the slice emits. (Not an enumerated grammar element on its own,
# but the validator rejects anything else so a template cannot smuggle verbs.)
_HTTP_METHODS: frozenset[str] = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})

_VARS_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PLACEHOLDER_RE = re.compile(r"\{([a-z]+)\.([A-Za-z_][A-Za-z0-9_]*)\}")
_PATH_ROOTS = ("vars", "input", "params")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class TemplateError(ValueError):
    """A static problem with a template: it never reaches a provider."""


class TemplateValidationError(TemplateError):
    """A mapping failed validation; it carries the list of human-readable errors."""

    def __init__(self, errors: Sequence[str]) -> None:
        super().__init__("; ".join(errors) or "template validation failed")
        self.errors = tuple(errors)


class TemplateMissing(TemplateError):
    """A referenced path was missing or null at evaluation time.

    The adapter turns this into a malformed response, *except* when it is caught
    by ``default(x, fallback)`` (Section 2.10).
    """

    def __init__(self, path: str) -> None:
        super().__init__(f"template path not found: {path}")
        self.path = path


class TemplateEvaluationError(TemplateError):
    """A conversion or image-sniff failed at evaluation time (malformed response)."""


# ---------------------------------------------------------------------------
# Evaluation context
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TemplateContext:
    """The roots a value form may read.

    ``item`` is set while evaluating inside ``$map`` / ``fields`` and is absent
    otherwise. ``max_batch`` bounds ``$map`` at evaluation time, matching the
    route's ``limits.max_batch``.
    """

    input: Mapping[str, Any] = field(default_factory=dict)
    params: Mapping[str, Any] = field(default_factory=dict)
    vars: Mapping[str, Any] = field(default_factory=dict)
    response: Any = None
    item: Any = None
    has_item: bool = False
    max_batch: int = 1

    def with_item(self, item: Any) -> TemplateContext:
        return TemplateContext(
            input=self.input,
            params=self.params,
            vars=self.vars,
            response=self.response,
            item=item,
            has_item=True,
            max_batch=self.max_batch,
        )

    def with_response(self, response: Any) -> TemplateContext:
        return TemplateContext(
            input=self.input,
            params=self.params,
            vars=self.vars,
            response=response,
            item=self.item,
            has_item=self.has_item,
            max_batch=self.max_batch,
        )

    def with_vars(self, vars_: Mapping[str, Any]) -> TemplateContext:
        return TemplateContext(
            input=self.input,
            params=self.params,
            vars=vars_,
            response=self.response,
            item=self.item,
            has_item=self.has_item,
            max_batch=self.max_batch,
        )


_MISSING = object()


# ---------------------------------------------------------------------------
# Multipart bodies (Tier 3, Task 28). A ``$multipart`` value form evaluates to
# a :class:`MultipartBody`: a map of text/data parts plus a list of file parts.
# The adapter turns it into httpx ``data``/``files``; the engine never builds a
# multipart wire body itself (no string building beyond the listed functions).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class MultipartFilePart:
    """One multipart file part: a form field carrying request bytes."""

    field: str
    content: bytes
    filename: str | None = None
    content_type: str | None = None


@dataclass(frozen=True)
class MultipartBody:
    """A multipart/form-data request body: text/data parts plus file parts."""

    data: Mapping[str, Any] = field(default_factory=dict)
    files: tuple[MultipartFilePart, ...] = ()


# ---------------------------------------------------------------------------
# $ref path parsing and resolution
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _Segment:
    kind: str  # "key" | "index" | "all" | "var_key"
    value: Any = None  # key name, int index, or var name


def _parse_ref_path(path: str) -> tuple[str, tuple[_Segment, ...]]:
    """Parse ``root.a[0].{vars.name}.b`` into a root and a segment list.

    Raises :class:`TemplateError` on a syntactically invalid path.
    """
    if not isinstance(path, str) or not path:
        raise TemplateError("ref path must be a non-empty string")
    # Split the root off the first dot or bracket.
    match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)", path)
    if not match:
        raise TemplateError(f"ref path has no root: {path!r}")
    root = match.group(1)
    if root not in REF_ROOTS:
        raise TemplateError(f"ref root {root!r} is not one of {REF_ROOTS}")
    rest = path[match.end() :]
    segments: list[_Segment] = []
    index = 0
    n = len(rest)
    while index < n:
        ch = rest[index]
        if ch == ".":
            index += 1
            # A ``{vars.name}`` variable key, or a plain key.
            if index < n and rest[index] == "{":
                close = rest.find("}", index)
                if close == -1:
                    raise TemplateError(f"unterminated variable segment in {path!r}")
                inner = rest[index + 1 : close]
                var_match = re.fullmatch(r"vars\.([A-Za-z_][A-Za-z0-9_]*)", inner)
                if not var_match:
                    raise TemplateError(
                        f"variable segment must be {{vars.<name>}} in {path!r}"
                    )
                segments.append(_Segment("var_key", var_match.group(1)))
                index = close + 1
                continue
            key_match = re.match(r"[A-Za-z_][A-Za-z0-9_]*", rest[index:])
            if not key_match:
                raise TemplateError(f"invalid key segment in {path!r}")
            segments.append(_Segment("key", key_match.group(0)))
            index += key_match.end()
        elif ch == "[":
            close = rest.find("]", index)
            if close == -1:
                raise TemplateError(f"unterminated index segment in {path!r}")
            inner = rest[index + 1 : close]
            if inner == "*":
                segments.append(_Segment("all"))
            elif re.fullmatch(r"\d+", inner):
                segments.append(_Segment("index", int(inner)))
            else:
                raise TemplateError(f"index segment must be [n] or [*] in {path!r}")
            index = close + 1
        else:
            raise TemplateError(f"unexpected character {ch!r} in path {path!r}")
    return root, tuple(segments)


def _root_value(ctx: TemplateContext, root: str, path: str) -> Any:
    if root == "input":
        return ctx.input
    if root == "params":
        return ctx.params
    if root == "vars":
        return ctx.vars
    if root == "response":
        return ctx.response
    if root == "item":
        if not ctx.has_item:
            raise TemplateMissing(path)
        return ctx.item
    raise TemplateError(f"ref root {root!r} is not one of {REF_ROOTS}")


def _resolve_segment(current: Any, segment: _Segment, ctx: TemplateContext, path: str) -> Any:
    if segment.kind == "key":
        if isinstance(current, Mapping) and segment.value in current:
            return current[segment.value]
        raise TemplateMissing(path)
    if segment.kind == "var_key":
        if segment.value not in ctx.vars:
            raise TemplateMissing(path)
        key = ctx.vars[segment.value]
        if isinstance(current, Mapping) and key in current:
            return current[key]
        raise TemplateMissing(path)
    if segment.kind == "index":
        if (
            isinstance(current, Sequence)
            and not isinstance(current, str | bytes | bytearray)
            and 0 <= segment.value < len(current)
        ):
            return current[segment.value]
        raise TemplateMissing(path)
    if segment.kind == "all":
        if isinstance(current, Sequence) and not isinstance(current, str | bytes | bytearray):
            return list(current)
        raise TemplateMissing(path)
    raise TemplateError(f"unknown segment kind {segment.kind!r}")


def resolve_ref(path: str, ctx: TemplateContext) -> Any:
    """Resolve a ``$ref`` path against ``ctx``.

    A ``[*]`` segment maps the remaining segments over each element. Raises
    :class:`TemplateMissing` when any segment is absent.
    """
    root, segments = _parse_ref_path(path)
    current = _root_value(ctx, root, path)
    for position, segment in enumerate(segments):
        if segment.kind == "all":
            if not (
                isinstance(current, Sequence)
                and not isinstance(current, str | bytes | bytearray)
            ):
                raise TemplateMissing(path)
            remaining = segments[position + 1 :]
            results = []
            for element in current:
                value = element
                for sub in remaining:
                    value = _resolve_segment(value, sub, ctx, path)
                results.append(value)
            return results
        current = _resolve_segment(current, segment, ctx, path)
    return current


def _starts_with_root(path: str) -> bool:
    match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)", path)
    return bool(match) and match.group(1) in REF_ROOTS


def resolve_relative_ref(path: str, ctx: TemplateContext) -> Any:
    """Resolve a path that may be relative to the current ``item``.

    Section 2.10 shorthands: inside ``fields`` and artifact keys a bare string is
    a ``$ref`` path relative to the current item (root ``item``). A path that
    already names one of the five roots is resolved as-is.
    """
    if _starts_with_root(path):
        return resolve_ref(path, ctx)
    return resolve_ref(f"item.{path}", ctx)


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------
def _fn_base64(args: list[Any]) -> str:
    import base64 as _base64

    (value,) = _expect_args("base64", args, 1)
    data = _as_bytes("base64", value)
    return _base64.b64encode(data).decode("ascii")


def _fn_data_url(args: list[Any]) -> str:
    import base64 as _base64

    (value,) = _expect_args("data_url", args, 1)
    data = _as_bytes("data_url", value)
    mime = sniff_image_mime(data)
    if mime is None:
        raise TemplateEvaluationError("data_url could not sniff a supported image format")
    encoded = _base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _fn_uuid(args: list[Any]) -> str:
    _expect_args("uuid", args, 0)
    return str(_uuid.uuid4())


def _fn_concat(args: list[Any]) -> str:
    parts: list[str] = []
    for value in args:
        if value is None:
            raise TemplateEvaluationError("concat received a null argument")
        if isinstance(value, bool | Mapping) or isinstance(value, Sequence) and not isinstance(
            value, str
        ):
            raise TemplateEvaluationError("concat only joins string-like scalars")
        parts.append(value if isinstance(value, str) else _scalar_str(value))
    return "".join(parts)


def _fn_to_int(args: list[Any]) -> int:
    (value,) = _expect_args("to_int", args, 1)
    try:
        if isinstance(value, bool):
            raise TypeError
        return int(value)
    except (TypeError, ValueError) as exc:
        raise TemplateEvaluationError("to_int could not convert the value") from exc


def _fn_to_float(args: list[Any]) -> float:
    (value,) = _expect_args("to_float", args, 1)
    try:
        if isinstance(value, bool):
            raise TypeError
        return float(value)
    except (TypeError, ValueError) as exc:
        raise TemplateEvaluationError("to_float could not convert the value") from exc


def _fn_to_ms(args: list[Any]) -> int:
    (value,) = _expect_args("to_ms", args, 1)
    try:
        if isinstance(value, bool):
            raise TypeError
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise TemplateEvaluationError("to_ms could not convert the value") from exc
    # Parity with the archived diarization adapter: round(seconds * 1000).
    return round(seconds * 1000)


def _fn_clamp01(args: list[Any]) -> float:
    (value,) = _expect_args("clamp01", args, 1)
    try:
        if isinstance(value, bool):
            raise TypeError
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise TemplateEvaluationError("clamp01 could not convert the value") from exc
    return max(0.0, min(1.0, number))


def _fn_default(args: list[Any]) -> Any:
    # default() is handled specially in _eval_fn so a missing path does not raise.
    raise AssertionError("default() must be routed through _eval_fn")


_FUNCTION_IMPLS = {
    "base64": _fn_base64,
    "data_url": _fn_data_url,
    "uuid": _fn_uuid,
    "concat": _fn_concat,
    "to_int": _fn_to_int,
    "to_float": _fn_to_float,
    "to_ms": _fn_to_ms,
    "clamp01": _fn_clamp01,
    "default": _fn_default,
}


def _expect_args(name: str, args: list[Any], count: int) -> list[Any]:
    if len(args) != count:
        raise TemplateEvaluationError(
            f"{name} expects {count} argument(s), got {len(args)}"
        )
    return args


def _as_bytes(name: str, value: Any) -> bytes:
    if isinstance(value, bytes | bytearray):
        return bytes(value)
    raise TemplateEvaluationError(f"{name} expects request bytes")


def _scalar_str(value: Any) -> str:
    if isinstance(value, bool):
        raise TemplateEvaluationError("concat does not accept booleans")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        # Keep integral floats tidy (2.0 -> "2") for opaque identifiers.
        return str(int(value)) if value.is_integer() else repr(value)
    raise TemplateEvaluationError("concat only joins string-like scalars")


def sniff_image_mime(data: bytes) -> str | None:
    """Return an image MIME for png/jpeg/webp/gif, or None for unknown formats."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return IMAGE_MIME_BY_FORMAT["png"]
    if data.startswith(b"\xff\xd8\xff"):
        return IMAGE_MIME_BY_FORMAT["jpeg"]
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return IMAGE_MIME_BY_FORMAT["webp"]
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return IMAGE_MIME_BY_FORMAT["gif"]
    return None


# ---------------------------------------------------------------------------
# Value-form evaluation
# ---------------------------------------------------------------------------
def evaluate_value(value: Any, ctx: TemplateContext) -> Any:
    """Evaluate one value form against ``ctx``.

    * A ``Mapping`` with a single ``$ref`` / ``$fn`` / ``$map`` key is that form.
    * Any other ``Mapping`` is a JSON object whose values are each evaluated.
    * A ``list`` is a JSON array whose items are each evaluated.
    * Anything else is a JSON literal and is returned as-is.
    """
    if isinstance(value, Mapping):
        if "$ref" in value:
            _reject_extra_keys("$ref", value)
            return resolve_ref(_require_str("$ref", value["$ref"]), ctx)
        if "$fn" in value:
            return _eval_fn(value, ctx)
        if "$map" in value:
            _reject_extra_keys("$map", value)
            return _eval_map(value["$map"], ctx)
        if "$multipart" in value:
            _reject_extra_keys("$multipart", value)
            return _eval_multipart(value["$multipart"], ctx)
        return {key: evaluate_value(val, ctx) for key, val in value.items()}
    if isinstance(value, list):
        return [evaluate_value(item, ctx) for item in value]
    return value


def _eval_multipart(spec: Any, ctx: TemplateContext) -> MultipartBody:
    """Evaluate a ``$multipart`` body (Tier 3, Task 28).

    Each field is either a value form (a plain text/data part) or a
    ``{"$file": <ref>, "filename": ..., "content_type": ...}`` file part whose
    ``$file`` resolves to request bytes. The adapter turns the returned
    :class:`MultipartBody` into httpx ``data``/``files`` — no string building,
    no second host (Section 2.10 "Not allowed").
    """
    if not isinstance(spec, Mapping):
        raise TemplateError("$multipart must be an object of field -> part")
    data: dict[str, Any] = {}
    files: list[MultipartFilePart] = []
    for name, part in spec.items():
        if not isinstance(name, str):
            raise TemplateError("$multipart field name must be a string")
        if isinstance(part, Mapping) and "$file" in part:
            extra = set(part.keys()) - set(MULTIPART_PART_KEYS) - {"$file"}
            if extra:
                raise TemplateError(f"$multipart file part has unexpected keys {sorted(extra)}")
            raw = evaluate_value(part["$file"], ctx)
            content = _as_bytes("$multipart $file", raw)
            filename = part.get("filename")
            content_type = part.get("content_type")
            files.append(
                MultipartFilePart(
                    field=name,
                    content=content,
                    filename=str(filename) if filename is not None else None,
                    content_type=str(content_type) if content_type is not None else None,
                )
            )
        else:
            data[name] = evaluate_value(part, ctx)
    return MultipartBody(data=data, files=tuple(files))


def _eval_fn(value: Mapping[str, Any], ctx: TemplateContext) -> Any:
    name = value.get("$fn")
    if not isinstance(name, str) or name not in FUNCTIONS:
        raise TemplateError(f"unknown function: {name!r}")
    extra = set(value.keys()) - {"$fn", "args"}
    if extra:
        raise TemplateError(f"$fn has unexpected keys: {sorted(extra)}")
    raw_args = value.get("args", [])
    if not isinstance(raw_args, list):
        raise TemplateError("$fn args must be a list")
    if name == "default":
        if len(raw_args) != 2:
            raise TemplateEvaluationError("default expects 2 arguments")
        try:
            primary = evaluate_value(raw_args[0], ctx)
        except TemplateMissing:
            primary = None
        if primary is None:
            return evaluate_value(raw_args[1], ctx)
        return primary
    args = [evaluate_value(arg, ctx) for arg in raw_args]
    return _FUNCTION_IMPLS[name](args)


def _eval_map(spec: Any, ctx: TemplateContext) -> list[Any]:
    if not isinstance(spec, Mapping):
        raise TemplateError("$map must be an object with 'over' and 'each'")
    extra = set(spec.keys()) - {"over", "each"}
    if extra:
        raise TemplateError(f"$map has unexpected keys: {sorted(extra)}")
    over = spec.get("over")
    if "each" not in spec:
        raise TemplateError("$map requires an 'each' value")
    items = resolve_ref(_require_str("over", over), ctx)
    if not isinstance(items, Sequence) or isinstance(items, str | bytes | bytearray):
        raise TemplateEvaluationError("$map 'over' did not resolve to a list")
    if len(items) > ctx.max_batch:
        raise TemplateError(
            f"$map over {len(items)} items exceeds max_batch {ctx.max_batch}"
        )
    return [evaluate_value(spec["each"], ctx.with_item(item)) for item in items]


def _reject_extra_keys(form: str, value: Mapping[str, Any]) -> None:
    if len(value) != 1:
        extra = sorted(set(value.keys()) - {form})
        raise TemplateError(f"{form} must be the only key; found extra {extra}")


def _require_str(what: str, value: Any) -> str:
    if not isinstance(value, str):
        raise TemplateError(f"{what} must be a string")
    return value


# ---------------------------------------------------------------------------
# Request-path placeholders
# ---------------------------------------------------------------------------
def render_path(path: str, ctx: TemplateContext) -> str:
    """Render ``/jobs/{vars.job_id}`` with percent-encoded substitutions.

    Scheme, host and port can never be templated (Section 2.10). The result must
    be an absolute path (starts with ``/``), with no scheme, no ``//`` prefix,
    and no ``..`` segment after substitution. Values are percent-encoded so a
    substituted value cannot escape its segment.
    """
    if not isinstance(path, str):
        raise TemplateError("request path must be a string")

    def _sub(match: re.Match[str]) -> str:
        root, name = match.group(1), match.group(2)
        if root not in _PATH_ROOTS:
            raise TemplateError(
                f"path placeholder root must be one of {_PATH_ROOTS}: {match.group(0)}"
            )
        value = resolve_ref(f"{root}.{name}", ctx)
        if value is None or isinstance(value, bool | Mapping | list):
            raise TemplateEvaluationError(
                f"path placeholder {match.group(0)} must resolve to a scalar"
            )
        return quote(value if isinstance(value, str) else _scalar_str(value), safe="")

    rendered = _PLACEHOLDER_RE.sub(_sub, path)
    _check_rendered_path(rendered)
    return rendered


def _check_rendered_path(path: str) -> None:
    if "{" in path or "}" in path:
        raise TemplateError(f"request path has an unresolved placeholder: {path!r}")
    if "://" in path or re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*:", path):
        raise TemplateError("request path may not contain a scheme")
    if not path.startswith("/"):
        raise TemplateError("request path must start with '/'")
    if path.startswith("//"):
        raise TemplateError("request path may not start with '//'")
    if any(segment == ".." for segment in path.split("?", 1)[0].split("/")):
        raise TemplateError("request path may not contain '..' segments")


# ---------------------------------------------------------------------------
# Static validation (static path check, before activation)
# ---------------------------------------------------------------------------
def _validate_path_static(path: str, errors: list[str], where: str) -> None:
    """Validate a path string with no substitution (host/scheme/traversal)."""
    if not isinstance(path, str):
        errors.append(f"{where}: path must be a string")
        return
    if "://" in path or re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*:", path):
        errors.append(f"{where}: path may not be an absolute URL or contain a scheme")
        return
    if not path.startswith("/"):
        errors.append(f"{where}: path must start with '/'")
    if path.startswith("//"):
        errors.append(f"{where}: path may not start with '//'")
    # Static segments (ignoring placeholders) must not contain traversal.
    stripped = _PLACEHOLDER_RE.sub("x", path)
    if any(segment == ".." for segment in stripped.split("?", 1)[0].split("/")):
        errors.append(f"{where}: path may not contain '..' segments")
    for match in _PLACEHOLDER_RE.finditer(path):
        if match.group(1) not in _PATH_ROOTS:
            errors.append(
                f"{where}: path placeholder root {match.group(1)!r} is not allowed"
            )
    # A lone '{' or '}' that is not a well-formed placeholder is rejected.
    residue = _PLACEHOLDER_RE.sub("", path)
    if "{" in residue or "}" in residue:
        errors.append(f"{where}: malformed path placeholder")


def _validate_value_form(
    value: Any, errors: list[str], where: str, *, allow_multipart: bool = False
) -> None:
    """Validate a value form statically (unknown $fn, unknown keys, roots, $map bound)."""
    if isinstance(value, Mapping):
        special = [key for key in ("$ref", "$fn", "$map", "$multipart") if key in value]
        if len(special) > 1:
            errors.append(
                f"{where}: a value form may use only one of $ref/$fn/$map/$multipart"
            )
            return
        if "$ref" in value:
            _reject_other_keys(value, "$ref", errors, where)
            _validate_ref_static(value["$ref"], errors, where)
            return
        if "$fn" in value:
            _validate_fn_static(value, errors, where)
            return
        if "$map" in value:
            _reject_other_keys(value, "$map", errors, where)
            _validate_map_static(value["$map"], errors, where)
            return
        if "$multipart" in value:
            if not allow_multipart:
                errors.append(f"{where}: $multipart is only allowed as a request body")
                return
            _reject_other_keys(value, "$multipart", errors, where)
            _validate_multipart_static(value["$multipart"], errors, where)
            return
        for key, val in value.items():
            if isinstance(key, str) and key.startswith("$"):
                errors.append(f"{where}: unknown special key {key!r}")
            _validate_value_form(val, errors, f"{where}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_value_form(item, errors, f"{where}[{index}]")


def _validate_multipart_static(spec: Any, errors: list[str], where: str) -> None:
    """Validate a ``$multipart`` body form statically (Tier 3, Task 28)."""
    if not isinstance(spec, Mapping) or not spec:
        errors.append(f"{where}: $multipart must be a non-empty object of field -> part")
        return
    for name, part in spec.items():
        if not isinstance(name, str) or not name:
            errors.append(f"{where}: $multipart field name must be a non-empty string")
            continue
        field_where = f"{where}.{name}"
        if isinstance(part, Mapping) and "$file" in part:
            extra = sorted(set(part.keys()) - set(MULTIPART_PART_KEYS) - {"$file"})
            if extra:
                errors.append(f"{field_where}: $multipart file part unexpected keys {extra}")
            _validate_value_form(part["$file"], errors, f"{field_where}.$file")
            for meta in ("filename", "content_type"):
                if meta in part and not isinstance(part[meta], str):
                    errors.append(f"{field_where}.{meta}: must be a string")
        else:
            _validate_value_form(part, errors, field_where)


def _reject_other_keys(value: Mapping[str, Any], form: str, errors: list[str], where: str) -> None:
    extra = sorted(set(value.keys()) - {form})
    if extra:
        errors.append(f"{where}: {form} must be the only key; found {extra}")


def _validate_ref_static(path: Any, errors: list[str], where: str) -> None:
    if not isinstance(path, str):
        errors.append(f"{where}: $ref path must be a string")
        return
    try:
        _parse_ref_path(path)
    except TemplateError as exc:
        errors.append(f"{where}: {exc}")


def _validate_fn_static(value: Mapping[str, Any], errors: list[str], where: str) -> None:
    name = value.get("$fn")
    if not isinstance(name, str) or name not in FUNCTIONS:
        errors.append(f"{where}: unknown function {name!r}")
        return
    extra = sorted(set(value.keys()) - {"$fn", "args"})
    if extra:
        errors.append(f"{where}: $fn has unexpected keys {extra}")
    args = value.get("args", [])
    if not isinstance(args, list):
        errors.append(f"{where}: $fn args must be a list")
        return
    _ARITY = {
        "uuid": 0,
        "base64": 1,
        "data_url": 1,
        "to_int": 1,
        "to_float": 1,
        "to_ms": 1,
        "clamp01": 1,
        "default": 2,
    }
    expected = _ARITY.get(name)
    if expected is not None and len(args) != expected:
        errors.append(f"{where}: {name} expects {expected} argument(s)")
    if name == "concat" and len(args) < 1:
        errors.append(f"{where}: concat expects at least one argument")
    for index, arg in enumerate(args):
        _validate_value_form(arg, errors, f"{where}.args[{index}]")


def _validate_map_static(spec: Any, errors: list[str], where: str) -> None:
    if not isinstance(spec, Mapping):
        errors.append(f"{where}: $map must be an object with 'over' and 'each'")
        return
    extra = sorted(set(spec.keys()) - {"over", "each"})
    if extra:
        errors.append(f"{where}: $map has unexpected keys {extra}")
    if "over" not in spec:
        errors.append(f"{where}: $map requires 'over'")
    else:
        _validate_ref_static(spec["over"], errors, f"{where}.over")
    if "each" not in spec:
        errors.append(f"{where}: $map requires 'each'")
    else:
        _validate_value_form(spec["each"], errors, f"{where}.each")


def _validate_bare_ref(
    value: Any, errors: list[str], where: str, *, relative: bool = False
) -> None:
    """A bare string shorthand is a $ref path (Section 2.10 shorthands).

    When ``relative`` is true (``fields`` and artifact keys), a path that does
    not name one of the five roots is implicitly relative to ``item``.
    """
    if not isinstance(value, str):
        errors.append(f"{where}: must be a $ref path string")
        return
    probe = value if _starts_with_root(value) or not relative else f"item.{value}"
    try:
        _parse_ref_path(probe)
    except TemplateError as exc:
        errors.append(f"{where}: {exc}")


def _validate_generic_http(
    mapping: Mapping[str, Any], errors: list[str]
) -> None:
    unknown = sorted(set(mapping.keys()) - ACCEPTED_GENERIC_HTTP_KEYS)
    if unknown:
        errors.append(f"$: unknown generic_http keys {unknown}")

    # Output discriminator (Tier 3). Defaults to Tier 1 object detection.
    output_kind = mapping.get("output", "object_detection")
    if output_kind not in GENERIC_HTTP_OUTPUT_KINDS:
        errors.append(f"$.output: must be one of {list(GENERIC_HTTP_OUTPUT_KINDS)}")
        output_kind = "object_detection"

    request = mapping.get("request")
    if not isinstance(request, Mapping):
        errors.append("$.request: required object with method, path and body")
    else:
        req_unknown = sorted(set(request.keys()) - set(GENERIC_HTTP_REQUEST_KEYS))
        if req_unknown:
            errors.append(f"$.request: unknown keys {req_unknown}")
        method = request.get("method")
        if not isinstance(method, str) or method.upper() not in _HTTP_METHODS:
            errors.append(f"$.request.method: must be one of {sorted(_HTTP_METHODS)}")
        if "path" not in request:
            errors.append("$.request.path: required")
        else:
            _validate_path_static(request["path"], errors, "$.request.path")
        if "body" in request:
            _validate_value_form(
                request["body"], errors, "$.request.body", allow_multipart=True
            )

    # ``items`` is required for list-shaped outputs (object detection and
    # embeddings), and forbidden for the single-result caption shape where the
    # whole response is one caption. Transcription reads the whole response too.
    list_shaped = output_kind in {"object_detection", "text_embeddings", "image_embeddings"}
    if "items" in mapping:
        _validate_bare_ref(mapping["items"], errors, "$.items")
    elif list_shaped:
        errors.append("$.items: required result-list path")

    if "aligned_to_input" in mapping and not isinstance(
        mapping["aligned_to_input"], bool
    ):
        errors.append("$.aligned_to_input: must be a boolean")

    allowed_field_keys = _FIELD_KEYS_BY_OUTPUT[output_kind]
    fields = mapping.get("fields")
    if not isinstance(fields, Mapping):
        errors.append("$.fields: required object of output field paths")
    else:
        field_unknown = sorted(set(fields.keys()) - allowed_field_keys)
        if field_unknown:
            errors.append(
                f"$.fields: unknown field keys {field_unknown} for output {output_kind!r}"
            )
        for key, path in fields.items():
            _validate_bare_ref(path, errors, f"$.fields.{key}", relative=True)

    box_format = mapping.get("box_format")
    if box_format is not None:
        if output_kind != "object_detection":
            errors.append("$.box_format: only valid for object_detection output")
        elif box_format not in ACCEPTED_BOX_FORMATS:
            errors.append(f"$.box_format: must be one of {sorted(ACCEPTED_BOX_FORMATS)}")

    time_unit = mapping.get("time_unit")
    if time_unit is not None:
        if output_kind != "transcription":
            errors.append("$.time_unit: only valid for transcription output")
        elif time_unit not in TRANSCRIPTION_TIME_UNITS:
            errors.append(f"$.time_unit: must be one of {list(TRANSCRIPTION_TIME_UNITS)}")

    if "item_error" in mapping:
        _validate_bare_ref(mapping["item_error"], errors, "$.item_error", relative=True)
    item_error_codes = mapping.get("item_error_codes")
    if item_error_codes is not None:
        if not isinstance(item_error_codes, Mapping):
            errors.append("$.item_error_codes: must be an object of code -> class")
        else:
            for code, klass in item_error_codes.items():
                if klass not in ITEM_ERROR_CLASSES:
                    errors.append(
                        f"$.item_error_codes.{code}: class must be one of "
                        f"{list(ITEM_ERROR_CLASSES)}"
                    )

    error_map = mapping.get("error_map")
    if error_map is not None:
        if not isinstance(error_map, Mapping):
            errors.append("$.error_map: must be an object of status -> class")
        else:
            for status, klass in error_map.items():
                if not re.fullmatch(r"\d{3}", str(status)):
                    errors.append(f"$.error_map.{status}: status must be a 3-digit code")
                if klass not in ERROR_MAP_CLASSES:
                    errors.append(
                        f"$.error_map.{status}: class must be one of "
                        f"{list(ERROR_MAP_CLASSES)}"
                    )

    usage = mapping.get("usage")
    if usage is not None and usage not in ACCEPTED_USAGE_MODES:
        errors.append(f"$.usage: must be one of {sorted(ACCEPTED_USAGE_MODES)}")
    usage_path = mapping.get("usage_path")
    if usage == "response_path" and usage_path is None:
        errors.append("$.usage_path: required when usage is 'response_path'")
    if usage_path is not None:
        if usage != "response_path":
            errors.append("$.usage_path: only valid when usage is 'response_path'")
        else:
            _validate_bare_ref(usage_path, errors, "$.usage_path")


# ---------------------------------------------------------------------------
# async_job static validation (Task 16B).
#
# These enforce the Section 2.10 "async_job keys" inventory and the fixed rules:
# strict unknown-key rejection, ``auth: none`` steps must use ``url`` (never
# ``path``) and adapter steps must use ``path`` (never ``url``), ``commit`` at
# most once and not on an ``auth: none`` step, ``poll`` required, either
# ``result`` or ``artifacts`` required, and ``allowed_hosts`` required when
# ``artifacts`` is present.
# ---------------------------------------------------------------------------
def _validate_async_step(step: Any, errors: list[str], where: str) -> bool:
    """Validate one step. Returns whether this step sets ``commit: true``."""
    if not isinstance(step, Mapping):
        errors.append(f"{where}: step must be an object")
        return False
    unknown = sorted(set(step.keys()) - set(ASYNC_JOB_STEP_KEYS))
    if unknown:
        errors.append(f"{where}: unknown step keys {unknown}")

    method = step.get("method")
    if not isinstance(method, str) or method.upper() not in _HTTP_METHODS:
        errors.append(f"{where}.method: must be one of {sorted(_HTTP_METHODS)}")

    auth = step.get("auth", "adapter")
    if auth not in ASYNC_JOB_AUTH_MODES:
        errors.append(f"{where}.auth: must be one of {list(ASYNC_JOB_AUTH_MODES)}")

    has_path = "path" in step
    has_url = "url" in step
    if auth == "none":
        # A pre-signed URL step targets a captured URL, never a templated path.
        if not has_url or has_path:
            errors.append(f"{where}: an auth:none step must use 'url' and not 'path'")
        else:
            _validate_bare_ref(step["url"], errors, f"{where}.url")
    else:
        if not has_path or has_url:
            errors.append(f"{where}: an adapter-auth step must use 'path' and not 'url'")
        else:
            _validate_path_static(step["path"], errors, f"{where}.path")

    body = step.get("body")
    if body is not None:
        if body == "input.file":
            pass  # The raw-upload sentinel (Section 2.10 fixed rule).
        else:
            _validate_value_form(body, errors, f"{where}.body", allow_multipart=True)

    capture = step.get("capture")
    if capture is not None:
        if not isinstance(capture, Mapping):
            errors.append(f"{where}.capture: must be an object of name -> path")
        else:
            for name, path in capture.items():
                if not isinstance(name, str) or not _VARS_NAME_RE.fullmatch(name):
                    errors.append(f"{where}.capture: invalid capture name {name!r}")
                _validate_bare_ref(path, errors, f"{where}.capture.{name}")

    require = step.get("require")
    if require is not None:
        if not isinstance(require, Mapping):
            errors.append(f"{where}.require: must be an object of name -> predicate")
        else:
            for name, predicate in require.items():
                if predicate not in ASYNC_JOB_REQUIRE_PREDICATES:
                    errors.append(
                        f"{where}.require.{name}: predicate must be one of "
                        f"{list(ASYNC_JOB_REQUIRE_PREDICATES)}"
                    )

    commit = step.get("commit", False)
    if not isinstance(commit, bool):
        errors.append(f"{where}.commit: must be a boolean")
        commit = False
    if commit and auth == "none":
        errors.append(f"{where}: commit may not be set on an auth:none step")
    return bool(commit)


def _validate_async_poll(poll: Any, errors: list[str]) -> None:
    if not isinstance(poll, Mapping):
        errors.append("$.poll: required object")
        return
    unknown = sorted(set(poll.keys()) - set(ASYNC_JOB_POLL_KEYS))
    if unknown:
        errors.append(f"$.poll: unknown keys {unknown}")
    method = poll.get("method")
    if not isinstance(method, str) or method.upper() not in {"GET", "POST"}:
        errors.append("$.poll.method: must be GET or POST")
    if "path" not in poll:
        errors.append("$.poll.path: required")
    else:
        _validate_path_static(poll["path"], errors, "$.poll.path")
    if "body" in poll:
        _validate_value_form(poll["body"], errors, "$.poll.body")
    if "status" not in poll:
        errors.append("$.poll.status: required status path")
    else:
        _validate_bare_ref(poll["status"], errors, "$.poll.status")

    status_map = poll.get("status_map")
    if not isinstance(status_map, Mapping) or not status_map:
        errors.append("$.poll.status_map: required non-empty object")
    else:
        for value, entry in status_map.items():
            _validate_status_fields(entry, errors, f"$.poll.status_map.{value}")

    if "tolerate_retryable" in poll and not isinstance(poll["tolerate_retryable"], bool):
        errors.append("$.poll.tolerate_retryable: must be a boolean")
    for num_key in ("interval_s", "max_wait_s"):
        if num_key in poll and not _is_positive_number(poll[num_key]):
            errors.append(f"$.poll.{num_key}: must be a positive number")
    backoff = poll.get("backoff")
    if backoff is not None:
        if not isinstance(backoff, Mapping):
            errors.append("$.poll.backoff: must be an object")
        else:
            backoff_unknown = sorted(set(backoff.keys()) - set(ASYNC_JOB_BACKOFF_KEYS))
            if backoff_unknown:
                errors.append(f"$.poll.backoff: unknown keys {backoff_unknown}")
            if "factor" in backoff and not _is_positive_number(backoff["factor"]):
                errors.append("$.poll.backoff.factor: must be a positive number")
            if "max_interval_s" in backoff and not _is_positive_number(
                backoff["max_interval_s"]
            ):
                errors.append("$.poll.backoff.max_interval_s: must be a positive number")


def _validate_status_fields(entry: Any, errors: list[str], where: str) -> None:
    if not isinstance(entry, Mapping):
        errors.append(f"{where}: must be an object with a 'state'")
        return
    unknown = sorted(set(entry.keys()) - set(ASYNC_JOB_STATUS_FIELDS))
    if unknown:
        errors.append(f"{where}: unknown status entry keys {unknown}")
    state = entry.get("state")
    if state not in ASYNC_JOB_STATUS_STATES:
        errors.append(f"{where}.state: must be one of {list(ASYNC_JOB_STATUS_STATES)}")
    for flag in ("billable", "fallback"):
        if flag in entry and not isinstance(entry[flag], bool):
            errors.append(f"{where}.{flag}: must be a boolean")


def _validate_async_result(result: Any, errors: list[str]) -> None:
    if not isinstance(result, Mapping):
        errors.append("$.result: must be an object with items and fields")
        return
    unknown = sorted(set(result.keys()) - set(ASYNC_JOB_RESULT_KEYS))
    if unknown:
        errors.append(f"$.result: unknown keys {unknown}")
    if "items" not in result:
        errors.append("$.result.items: required result-list path")
    else:
        _validate_bare_ref(result["items"], errors, "$.result.items")
    fields = result.get("fields")
    if not isinstance(fields, Mapping) or not fields:
        errors.append("$.result.fields: required non-empty object of field paths")
    else:
        for key, spec in fields.items():
            _validate_field_value(spec, errors, f"$.result.fields.{key}")


def _validate_field_value(spec: Any, errors: list[str], where: str) -> None:
    """A result/artifact field is a value form, or a bare relative $ref string.

    A bare string is the Section 2.10 shorthand: a $ref path relative to the
    current item. A mapping/list is a full value form (so ``to_ms`` and friends
    can convert an extracted value).
    """
    if isinstance(spec, str):
        _validate_bare_ref(spec, errors, where, relative=True)
    else:
        _validate_value_form(spec, errors, where)


def _validate_async_artifacts(artifacts: Any, errors: list[str]) -> None:
    if not isinstance(artifacts, Mapping):
        errors.append("$.artifacts: must be an object")
        return
    unknown = sorted(set(artifacts.keys()) - set(ASYNC_JOB_ARTIFACT_KEYS))
    if unknown:
        errors.append(f"$.artifacts: unknown keys {unknown}")
    if "items" not in artifacts:
        errors.append("$.artifacts.items: required artifact-list path")
    else:
        _validate_bare_ref(artifacts["items"], errors, "$.artifacts.items")
    for key in ("name", "url"):
        if key not in artifacts:
            errors.append(f"$.artifacts.{key}: required")
        else:
            _validate_bare_ref(artifacts[key], errors, f"$.artifacts.{key}", relative=True)
    name_map = artifacts.get("name_map")
    if not isinstance(name_map, Mapping) or not name_map:
        errors.append("$.artifacts.name_map: required non-empty object")
    required = artifacts.get("required")
    if required is not None and not (
        isinstance(required, list) and all(isinstance(name, str) for name in required)
    ):
        errors.append("$.artifacts.required: must be a list of names")
    if "max_bytes" in artifacts and not (
        isinstance(artifacts["max_bytes"], int)
        and not isinstance(artifacts["max_bytes"], bool)
        and artifacts["max_bytes"] > 0
    ):
        errors.append("$.artifacts.max_bytes: must be a positive integer")
    allowed_hosts = artifacts.get("allowed_hosts")
    if not isinstance(allowed_hosts, list) or not allowed_hosts or not all(
        isinstance(host, str) and host for host in allowed_hosts
    ):
        errors.append("$.artifacts.allowed_hosts: required non-empty list of hosts")
    if "force_https" in artifacts and not isinstance(artifacts["force_https"], bool):
        errors.append("$.artifacts.force_https: must be a boolean")
    auth = artifacts.get("auth", "none")
    if auth != "none":
        errors.append("$.artifacts.auth: must be 'none'")


def _validate_async_cleanup(cleanup: Any, errors: list[str]) -> None:
    if not isinstance(cleanup, list):
        errors.append("$.cleanup: must be a list of requests")
        return
    for index, request in enumerate(cleanup):
        where = f"$.cleanup[{index}]"
        if not isinstance(request, Mapping):
            errors.append(f"{where}: must be an object")
            continue
        unknown = sorted(set(request.keys()) - set(ASYNC_JOB_CLEANUP_KEYS))
        if unknown:
            errors.append(f"{where}: unknown keys {unknown}")
        method = request.get("method")
        if not isinstance(method, str) or method.upper() not in _HTTP_METHODS:
            errors.append(f"{where}.method: must be one of {sorted(_HTTP_METHODS)}")
        if "path" not in request:
            errors.append(f"{where}.path: required")
        else:
            _validate_path_static(request["path"], errors, f"{where}.path")
        if "body" in request:
            _validate_value_form(request["body"], errors, f"{where}.body")


def _is_positive_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value > 0
    )


def _validate_async_job(mapping: Mapping[str, Any], errors: list[str]) -> None:
    unknown = sorted(set(mapping.keys()) - ACCEPTED_ASYNC_JOB_KEYS)
    if unknown:
        errors.append(f"$: unknown async_job keys {unknown}")

    # Non-blocking async mode (Tier 3, Task 28). Defaults to blocking so every
    # Tier 1 route keeps its current behaviour.
    mode = mapping.get(ASYNC_JOB_MODE_KEY, "blocking")
    if mode not in ASYNC_JOB_MODES:
        errors.append(f"$.mode: must be one of {list(ASYNC_JOB_MODES)}")

    vars_block = mapping.get("vars")
    if vars_block is not None:
        if not isinstance(vars_block, Mapping):
            errors.append("$.vars: must be an object of name -> value form")
        else:
            for name, value in vars_block.items():
                if not isinstance(name, str) or not _VARS_NAME_RE.fullmatch(name):
                    errors.append(f"$.vars: invalid variable name {name!r}")
                _validate_value_form(value, errors, f"$.vars.{name}")

    steps = mapping.get("steps")
    if not isinstance(steps, list) or not steps:
        errors.append("$.steps: required non-empty list of steps")
    else:
        commit_count = 0
        for index, step in enumerate(steps):
            if _validate_async_step(step, errors, f"$.steps[{index}]"):
                commit_count += 1
        if commit_count > 1:
            errors.append("$.steps: commit may be set on at most one step")

    if "poll" not in mapping:
        errors.append("$.poll: required")
    else:
        _validate_async_poll(mapping["poll"], errors)

    has_result = "result" in mapping
    has_artifacts = "artifacts" in mapping
    if not has_result and not has_artifacts:
        errors.append("$: either 'result' or 'artifacts' is required")
    if has_result:
        _validate_async_result(mapping["result"], errors)
    if has_artifacts:
        _validate_async_artifacts(mapping["artifacts"], errors)

    if "cleanup" in mapping:
        _validate_async_cleanup(mapping["cleanup"], errors)


def validate_mapping(
    adapter_type: str,
    mapping: Mapping[str, object],
    *,
    route: Mapping[str, object] | None = None,
) -> list[str]:
    """Validate a route ``mapping`` against the frozen Section 2.10 inventory.

    Returns a list of human-readable errors with JSON-ish paths; an empty list
    means the mapping is accepted. ``route`` carries route-level options (for
    example ``limits``), reserved for cross-checks.
    """
    del route  # Reserved: route limits/options drive cross-checks in the adapter.
    errors: list[str] = []
    if adapter_type == "generic_http":
        if not isinstance(mapping, Mapping):
            return ["$: mapping must be an object"]
        _validate_generic_http(mapping, errors)
        return errors
    if adapter_type == "async_job":
        if not isinstance(mapping, Mapping):
            return ["$: mapping must be an object"]
        _validate_async_job(mapping, errors)
        return errors
    return [f"$: unknown adapter type {adapter_type!r}"]
