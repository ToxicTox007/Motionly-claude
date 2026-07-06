import asyncio
import os
from collections.abc import Callable


async def run_blocking[T](func: Callable[..., T], /, *args: object, **kwargs: object) -> T:
    if os.getenv("MOTIONLY_DISABLE_THREAD_OFFLOAD") == "true" or os.getenv("PYTEST_CURRENT_TEST"):
        return func(*args, **kwargs)
    return await asyncio.to_thread(func, *args, **kwargs)
