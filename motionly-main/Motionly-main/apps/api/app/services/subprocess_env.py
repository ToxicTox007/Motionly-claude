"""Environments for child processes that handle untrusted media.

Worker processes hold the Supabase key, the worker API token, the Redis URL and
provider credentials. ffmpeg/ffprobe (and Demucs) parse user-supplied media, so
a decoder exploit would otherwise be able to read every one of those secrets
from its environment. These helpers forward an allowlist instead, so a newly
added secret is never handed to a media tool by accident.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

# What any media tool needs to run, find fonts and write temp files.
_MEDIA_TOOL_NAMES = frozenset(
    {
        "PATH",
        "HOME",
        "TMPDIR",
        "TMP",
        "TEMP",
        "LANG",
        "LANGUAGE",
        "TZ",
        "FONTCONFIG_PATH",
        "FONTCONFIG_FILE",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_RUNTIME_DIR",
        "LD_LIBRARY_PATH",
    }
)
_MEDIA_TOOL_PREFIXES = ("LC_",)

# Model tools (Demucs runs as a Python subprocess): interpreter, model caches,
# accelerator selection and thread limits on top of the media-tool set.
_ML_TOOL_NAMES = _MEDIA_TOOL_NAMES | {
    "VIRTUAL_ENV",
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONUNBUFFERED",
    "HF_HOME",
    "TORCH_HOME",
    "CUDA_HOME",
    "CUDA_VISIBLE_DEVICES",
    "NVIDIA_VISIBLE_DEVICES",
    "NVIDIA_DRIVER_CAPABILITIES",
    "PYTORCH_CUDA_ALLOC_CONF",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
}


def _pick(
    environ: Mapping[str, str], names: frozenset[str], prefixes: tuple[str, ...]
) -> dict[str, str]:
    return {
        name: value for name, value in environ.items() if name in names or name.startswith(prefixes)
    }


def media_tool_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Environment for ffmpeg / ffprobe."""
    return _pick(
        os.environ if environ is None else environ, _MEDIA_TOOL_NAMES, _MEDIA_TOOL_PREFIXES
    )


def ml_tool_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Environment for model subprocesses such as Demucs."""
    return _pick(os.environ if environ is None else environ, _ML_TOOL_NAMES, _MEDIA_TOOL_PREFIXES)
