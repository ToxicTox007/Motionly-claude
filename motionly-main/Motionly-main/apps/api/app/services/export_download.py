"""Download filename derivation for finished exports (Task 16).

Produces a safe ``Content-Disposition`` attachment filename from the project
title and export version. The result is restricted to a conservative character
set so it is safe to place in an HTTP header and in the Supabase Storage
``download`` query option across browsers and filesystems.
"""

from __future__ import annotations

import re

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_STEM_LENGTH = 80


def export_download_filename(*, title: str | None, version: int, extension: str) -> str:
    """Return a sanitized ``<title>-vN.<ext>`` download filename.

    Whitespace collapses to single hyphens; any character outside
    ``[A-Za-z0-9._-]`` is dropped. Falls back to ``export`` when the title
    sanitizes to nothing, so the filename is always well-formed.
    """
    raw = (title or "").strip()
    # Collapse internal whitespace to single hyphens before stripping.
    stem = _UNSAFE.sub("-", raw).strip("-._")
    stem = stem[:_MAX_STEM_LENGTH].strip("-._")
    if not stem:
        stem = "export"
    ext = (extension or "mp4").lstrip(".").lower()
    ext = _UNSAFE.sub("", ext) or "mp4"
    return f"{stem}-v{int(version)}.{ext}"
