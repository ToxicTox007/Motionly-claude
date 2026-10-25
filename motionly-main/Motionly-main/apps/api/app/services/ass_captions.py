"""Caption track -> ASS script for the FFmpeg (libass) renderer.

Caption text and style values are user-editable, so everything interpolated
into the script is neutralised for libass:

* Text is the last field of a ``Dialogue:`` line, so commas need no escaping
  (an escaped ``\\,`` would render literally).
* ``{`` / ``}`` delimit override tags; they are emitted as libass's literal
  ``\\{`` / ``\\}`` so caption text can never inject styling, drawings or
  transforms.
* A backslash could combine with the next character into an escape (``\\N``,
  ``\\h``, ``\\{``); a word joiner (U+2060, invisible) is placed after every
  user backslash so it always renders as itself.
* Line breaks of any kind become ``\\N`` and other control characters are
  dropped, so text cannot start a new script line.
* Style values (font family, colours) are reduced to safe forms before they
  reach the ``Style:`` line.
"""

import re

from app.schemas.captions import CaptionSegment, CaptionStyle

_DEFAULT_FONT_FAMILY = "Inter"
_MAX_FONT_FAMILY_LENGTH = 120
_WORD_JOINER = "\u2060"
# Every way text can break a line: CRLF/CR/LF, vertical tab, form feed, NEL,
# and the Unicode line/paragraph separators.
_NEWLINES = re.compile(r"\r\n|[\r\n\v\f\x85\u2028\u2029]")
# Remaining C0/C1 control characters (tab is treated as a space).
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0e-\x1f\x7f-\x84\x86-\x9f]")
# Characters that would end a Style field or a script line, or start a tag.
_FONT_FAMILY_UNSAFE = re.compile(r"[,{}\\\x00-\x1f\x7f-\x9f\u2028\u2029]")
_HEX_COLOR = re.compile(r"#?([0-9a-fA-F]{6})")
_WHITESPACE = re.compile(r"(\s+)")


def caption_track_to_ass(*, segments: list[CaptionSegment], style: CaptionStyle) -> str:
    alignment = {"lower": 2, "middle": 5, "upper": 8}[style.placement]
    margin_v = max(80, int(1920 * style.safe_area_bottom_pct / 100))
    primary = _ass_color(style.text_color)
    accent = _ass_color(style.accent_color)
    stroke = _ass_color(style.stroke_color or "#0f172a")
    back = _ass_color(style.background_color or "#000000", opacity=style.background_opacity)
    border_style = 3 if style.background_color and style.background_opacity > 0 else 1
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1080",
        "PlayResY: 1920",
        "",
        "[V4+ Styles]",
        (
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
            "ScaleY, Spacing, Angle, BorderStyle, Alignment, MarginL, MarginR, "
            "MarginV, Outline, Shadow, Encoding"
        ),
        (
            "Style: Motionly,"
            f"{_ass_font_family(style.font_family)},{style.font_size_px},"
            f"{primary},{accent},{stroke},{back},"
            f"{-1 if style.font_weight >= 700 else 0},0,0,0,100,100,"
            f"{style.letter_spacing_px},0,{border_style},{alignment},64,64,{margin_v},"
            f"{style.stroke_width_px},{1 if style.shadow else 0},1"
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Text",
    ]
    for segment in segments:
        text = _format_text(
            _mode_text(segment=segment, style=style),
            style,
            primary=primary,
            accent=accent,
            highlights=segment.highlight_words,
        )
        lines.append(
            f"Dialogue: 0,{_ass_time(segment.start_ms)},{_ass_time(segment.end_ms)},Motionly,{text}"
        )
    return "\n".join(lines) + "\n"


def _format_text(
    text: str,
    style: CaptionStyle,
    *,
    primary: str,
    accent: str,
    highlights: list[str],
) -> str:
    if style.text_case == "uppercase":
        text = text.upper()
    elif style.text_case == "title":
        text = text.title()
    highlight_keys = {key for word in highlights if (key := _highlight_key(word))}
    lines = _NEWLINES.split(text)
    return r"\N".join(
        _format_line(line, highlight_keys=highlight_keys, primary=primary, accent=accent)
        for line in lines
    )


def _format_line(line: str, *, highlight_keys: set[str], primary: str, accent: str) -> str:
    line = _CONTROL_CHARS.sub("", line.replace("\t", " "))
    parts: list[str] = []
    for token in _WHITESPACE.split(line):
        if not token or token.isspace():
            parts.append(token)
            continue
        # Whole words only, so a highlight can never match inside another word
        # or inside a colour tag inserted for an earlier highlight.
        if highlight_keys and _highlight_key(token) in highlight_keys:
            prefix, core, suffix = _split_punctuation(token)
            parts.append(
                f"{_escape_text(prefix)}{{\\c{accent}}}{_escape_text(core)}"
                f"{{\\c{primary}}}{_escape_text(suffix)}"
            )
        else:
            parts.append(_escape_text(token))
    return "".join(parts)


def _escape_text(text: str) -> str:
    # Backslashes first, so the brace escapes added next are not neutralised.
    return text.replace("\\", "\\" + _WORD_JOINER).replace("{", r"\{").replace("}", r"\}")


def _highlight_key(word: str) -> str:
    """The comparison form of a word, matching the editor and Remotion renderer.

    Both normalise words with ``word.replace(/[^\\w'-]/g, "")``; comparison is
    case-insensitive so highlights survive the uppercase/title text cases.
    """
    return re.sub(r"[^\w'-]", "", word).casefold()


def _split_punctuation(token: str) -> tuple[str, str, str]:
    """``("(", "word", "!)")`` for ``"(word!)"`` so only the word is recoloured."""
    match = re.match(r"^(\W*)(.*?)(\W*)$", token, flags=re.DOTALL)
    if match is None or not match.group(2):
        return "", token, ""
    return match.group(1), match.group(2), match.group(3)


def _mode_text(*, segment: CaptionSegment, style: CaptionStyle) -> str:
    if style.mode == "educational_card":
        return f"Step {segment.index + 1}: {segment.text}"
    if style.mode == "creator_ticker":
        return f"> {segment.text}"
    return segment.text


def _ass_font_family(font_family: str) -> str:
    """A font name that cannot end the ``Style:`` field or the script line."""
    cleaned = " ".join(_FONT_FAMILY_UNSAFE.sub(" ", font_family or "").split())
    return cleaned[:_MAX_FONT_FAMILY_LENGTH].strip() or _DEFAULT_FONT_FAMILY


def _ass_color(hex_color: str, *, opacity: float = 1) -> str:
    match = _HEX_COLOR.fullmatch((hex_color or "").strip())
    if match is None:
        return "&H00FFFFFF"
    normalized = match.group(1).upper()
    red, green, blue = normalized[0:2], normalized[2:4], normalized[4:6]
    alpha = max(0, min(255, round(255 * (1 - opacity))))
    return f"&H{alpha:02X}{blue}{green}{red}"


def _ass_time(ms: int) -> str:
    centiseconds = ms // 10
    hours, rem = divmod(centiseconds, 360_000)
    minutes, rem = divmod(rem, 6_000)
    seconds, cs = divmod(rem, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{cs:02d}"
