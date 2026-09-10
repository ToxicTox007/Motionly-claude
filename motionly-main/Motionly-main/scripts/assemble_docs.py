from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "docs" / "source"
OUTPUT_FILE = ROOT / "docs" / "generated" / "motionly_technical_documentation.md"
CHAPTER_FILES = [
    "part_1.md",
    "part_2.md",
    "part_3.md",
    "part_4.md",
    "part_5.md",
    "part_6.md",
    "part_7.md",
    "part_8.md",
    "part_9.md",
    "part_10.md",
    "appendices.md",
]
PAGE_BREAK = '<div style="page-break-after: always;"></div>'


def heading_anchor(title: str) -> str:
    anchor = (
        title.lower()
        .replace(" ", "-")
        .replace("—", "")
        .replace("&", "")
        .replace("/", "")
        .replace("(", "")
        .replace(")", "")
        .replace(".", "")
    )
    return "".join(character for character in anchor if character.isalnum() or character in "-_")


def assemble() -> None:
    missing = [filename for filename in CHAPTER_FILES if not (SOURCE_DIR / filename).exists()]
    if missing:
        missing_list = ", ".join(missing)
        raise FileNotFoundError(
            f"Documentation source chapters are missing from {SOURCE_DIR}: {missing_list}"
        )

    contents = [(SOURCE_DIR / filename).read_text(encoding="utf-8") for filename in CHAPTER_FILES]
    part_1_content = contents[0]
    if PAGE_BREAK not in part_1_content:
        raise ValueError(f"{SOURCE_DIR / CHAPTER_FILES[0]} is missing the required page-break marker")

    toc_lines = ["# Table of Contents", ""]
    for content in contents:
        for line in content.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                if title != "Motionly Technical & Product Documentation":
                    toc_lines.append(f"- [{title}](#{heading_anchor(title)})")
            elif line.startswith("## "):
                title = line[3:].strip()
                toc_lines.append(f"  - [{title}](#{heading_anchor(title)})")

    title_section, remainder = part_1_content.split(PAGE_BREAK, 1)
    final_markdown = [
        title_section.strip(),
        f"\n\n{PAGE_BREAK}\n\n",
        "\n".join(toc_lines),
        f"\n\n{PAGE_BREAK}\n\n",
        remainder.strip(),
    ]
    final_markdown.extend(f"\n\n{content.strip()}" for content in contents[1:])

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text("\n".join(final_markdown), encoding="utf-8")
    print(f"Successfully assembled documentation at {OUTPUT_FILE}")


if __name__ == "__main__":
    assemble()
