"""ASS caption generation: user text and style can never become libass markup."""

import re
import subprocess
from shutil import which

import pytest

from app.schemas.captions import CaptionSegment, CaptionStyle
from app.services.ass_captions import caption_track_to_ass

WORD_JOINER = "\u2060"


def _style(**overrides) -> CaptionStyle:
    return CaptionStyle(
        **{
            "font_family": "DejaVu Sans",
            "font_size_px": 70,
            "font_weight": 700,
            "text_case": "normal",
            "text_color": "#FFFFFF",
            "accent_color": "#FFD400",
            "safe_area_bottom_pct": 20,
            "placement": "middle",
            "animation": "pop",
            "contrast_guardrail": "auto",
            **overrides,
        }
    )


def _segment(text: str, highlights=()) -> CaptionSegment:
    return CaptionSegment(
        index=0, start_ms=0, end_ms=2000, text=text, highlight_words=list(highlights)
    )


def _dialogue_text(ass: str) -> str:
    dialogues = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
    assert len(dialogues) == 1, dialogues
    return dialogues[0].split(",", 4)[4]


def _style_fields(ass: str) -> list[str]:
    styles = [line for line in ass.splitlines() if line.startswith("Style:")]
    assert len(styles) == 1, styles
    return styles[0].removeprefix("Style: ").split(",")


def _override_blocks(text: str) -> list[str]:
    """Tag blocks libass would parse: ``{`` not preceded by a backslash escape."""
    return re.findall(r"(?<!\\)\{([^}]*)\}", text)


def test_commas_are_not_escaped() -> None:
    ass = caption_track_to_ass(segments=[_segment("Wait, really, yes")], style=_style())
    assert _dialogue_text(ass) == "Wait, really, yes"


def test_override_tags_in_caption_text_are_rendered_literally() -> None:
    text = r"{\fs220\c&H0000FF&\frz20}INJECTED {\p1}m 0 0 l 100 0{\p0}"
    ass = caption_track_to_ass(segments=[_segment(text)], style=_style())
    dialogue = _dialogue_text(ass)

    assert _override_blocks(dialogue) == []
    assert dialogue.startswith(r"\{" + "\\" + WORD_JOINER + "fs220")
    # Removing the escapes gives back exactly what the user typed.
    assert dialogue.replace(WORD_JOINER, "").replace(r"\{", "{").replace(r"\}", "}") == text


def test_backslash_escapes_in_caption_text_are_neutralised() -> None:
    ass = caption_track_to_ass(segments=[_segment(r"a\Nb a\nb a\hb trailing\ ")], style=_style())
    dialogue = _dialogue_text(ass)
    for escape in (r"\N", r"\n", r"\h"):
        assert escape not in dialogue
    assert dialogue.count("\\" + WORD_JOINER) == 4


def test_every_line_break_becomes_a_caption_line_break_not_a_script_line() -> None:
    text = "one\ntwo\r\nthree\rfour\u2028five\u2029six\x00\x1bseven\tend"
    ass = caption_track_to_ass(segments=[_segment(text)], style=_style())
    dialogue = _dialogue_text(ass)

    assert dialogue == r"one\Ntwo\Nthree\Nfour\Nfive\Nsixseven end"
    assert not re.search(r"[\x00-\x1f\u2028\u2029]", ass.replace("\n", ""))


def test_highlights_match_whole_words_only_and_never_nest_tags() -> None:
    ass = caption_track_to_ass(
        segments=[_segment("Hello world, Help! hello", highlights=["Hello", "H", "help", "x"])],
        style=_style(),
    )
    dialogue = _dialogue_text(ass)

    accent, primary = r"{\c&H0000D4FF}", r"{\c&H00FFFFFF}"
    assert dialogue == (
        f"{accent}Hello{primary} world, {accent}Help{primary}! {accent}hello{primary}"
    )
    assert all(block.startswith(r"\c&H") for block in _override_blocks(dialogue))


def test_highlights_survive_uppercase_and_title_case() -> None:
    for text_case, expected_word in (("uppercase", "LAUNCH"), ("title", "Launch")):
        ass = caption_track_to_ass(
            segments=[_segment("we launch today", highlights=["launch"])],
            style=_style(text_case=text_case),
        )
        assert rf"{{\c&H0000D4FF}}{expected_word}{{\c&H00FFFFFF}}" in _dialogue_text(ass)


def test_braces_around_a_highlighted_word_stay_literal() -> None:
    ass = caption_track_to_ass(
        segments=[_segment("{secret}", highlights=["secret"])], style=_style()
    )
    assert _dialogue_text(ass) == r"\{{\c&H0000D4FF}secret{\c&H00FFFFFF}\}"


def test_font_family_cannot_break_out_of_the_style_line() -> None:
    hostile = "Inter\nDialogue: 0,0:00:00.00,0:00:02.00,Motionly,{\\fs300}X"
    ass = caption_track_to_ass(segments=[_segment("ok")], style=_style(font_family=hostile))

    fields = _style_fields(ass)
    assert len(fields) == 23
    assert fields[1] == r"Inter Dialogue: 0 0:00:00.00 0:00:02.00 Motionly fs300 X"
    assert sum(line.startswith("Dialogue:") for line in ass.splitlines()) == 1

    blank = caption_track_to_ass(segments=[_segment("ok")], style=_style(font_family=" , \n"))
    assert _style_fields(blank)[1] == "Inter"
    long_name = caption_track_to_ass(segments=[_segment("ok")], style=_style(font_family="A" * 500))
    assert len(_style_fields(long_name)[1]) == 120


def test_colours_must_be_hex_or_fall_back() -> None:
    ass = caption_track_to_ass(
        segments=[_segment("ok", highlights=["ok"])],
        style=_style(text_color="F,\n,ab", accent_color="#00ff00", stroke_color="12,456"),
    )
    fields = _style_fields(ass)
    assert len(fields) == 23
    assert fields[3] == "&H00FFFFFF"  # invalid text colour: safe default
    assert fields[4] == "&H0000FF00"  # valid accent, normalised
    assert fields[5] == "&H00FFFFFF"  # invalid stroke colour: safe default


# --------------------------------------------------------------------------
# Real libass: injected markup does not change what is drawn
# --------------------------------------------------------------------------

requires_ffmpeg = pytest.mark.skipif(which("ffmpeg") is None, reason="ffmpeg is not installed")


def _red_pixels(tmp_path, ass: str) -> int:
    path = tmp_path / "captions.ass"
    path.write_text(ass, encoding="utf-8")
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=1080x1920:d=0.5",
            "-vf",
            f"ass={path},scale=108:192",
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        capture_output=True,
        check=True,
        timeout=30,
    )
    frame = result.stdout
    return sum(
        1
        for offset in range(0, len(frame), 3)
        if frame[offset] > 150 and frame[offset + 1] < 90 and frame[offset + 2] < 90
    )


@requires_ffmpeg
def test_libass_does_not_apply_override_tags_from_caption_text(tmp_path) -> None:
    injected = r"{\fs400\c&H0000FF&\an5}XXXX"
    ass = caption_track_to_ass(segments=[_segment(injected)], style=_style())
    assert _red_pixels(tmp_path, ass) == 0


@requires_ffmpeg
def test_control_libass_probe_sees_red_text(tmp_path) -> None:
    """The probe above can fail: a red accent highlight does draw red pixels."""
    ass = caption_track_to_ass(
        segments=[_segment("XXXX", highlights=["XXXX"])],
        style=_style(accent_color="#FF0000", font_size_px=200),
    )
    assert _red_pixels(tmp_path, ass) > 20
