import gc
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class CudaOomFailure(Exception):
    retryable: bool = True
    code: str = "cuda_oom"
    message: str = "GPU out of memory during visual analysis."


def is_cuda_oom_error(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    return (
        "outofmemoryerror" in name
        or "cuda out of memory" in message
        or ("cuda" in message and "out of memory" in message)
    )


def clear_cuda_cache(torch_module: object | None = None) -> bool:
    try:
        torch = torch_module
        if torch is None:
            import torch as imported_torch  # type: ignore[import-not-found]

            torch = imported_torch
        cuda = getattr(torch, "cuda", None)
        if cuda is None:
            gc.collect()
            return False
        is_available = getattr(cuda, "is_available", None)
        if callable(is_available) and not is_available():
            gc.collect()
            return False
        empty_cache = getattr(cuda, "empty_cache", None)
        if callable(empty_cache):
            empty_cache()
            gc.collect()
            return True
    except Exception:
        logger.warning("cuda_cache_cleanup_failed", exc_info=False)
    gc.collect()
    return False
