from app.schemas.captions import CaptionSegment, CaptionStyle


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
            f"{style.font_family},{style.font_size_px},{primary},{accent},{stroke},{back},"
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
        highlights = [word.upper() for word in highlights]
    elif style.text_case == "title":
        text = text.title()
        highlights = [word.title() for word in highlights]
    for word in sorted(set(highlights), key=len, reverse=True):
        if not word:
            continue
        text = text.replace(word, rf"{{\c{accent}}}{word}{{\c{primary}}}")
    return text.replace("\n", r"\N").replace(",", r"\,")


def _mode_text(*, segment: CaptionSegment, style: CaptionStyle) -> str:
    if style.mode == "educational_card":
        return f"Step {segment.index + 1}: {segment.text}"
    if style.mode == "creator_ticker":
        return f"> {segment.text}"
    return segment.text


def _ass_color(hex_color: str, *, opacity: float = 1) -> str:
    normalized = hex_color.lstrip("#")
    if len(normalized) != 6:
        return "&H00FFFFFF"
    red, green, blue = normalized[0:2], normalized[2:4], normalized[4:6]
    alpha = max(0, min(255, round(255 * (1 - opacity))))
    return f"&H{alpha:02X}{blue}{green}{red}"


def _ass_time(ms: int) -> str:
    centiseconds = ms // 10
    hours, rem = divmod(centiseconds, 360_000)
    minutes, rem = divmod(rem, 6_000)
    seconds, cs = divmod(rem, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{cs:02d}"
