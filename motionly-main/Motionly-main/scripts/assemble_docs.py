import os
from pathlib import Path

def assemble():
    workspace_dir = Path("/workspaces/Motionly-claude/motionly-main/Motionly-main")
    chapters_dir = workspace_dir / "docs" / "chapters"
    output_file = workspace_dir / "docs" / "motionly_technical_documentation.md"
    artifact_dir = Path("/home/codespace/.gemini/antigravity-cli/brain/4e6b6ebe-52fa-453e-be43-772ec8d4fa1a")
    artifact_file = artifact_dir / "motionly_technical_documentation.md"
    
    chapter_files = [
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
    
    contents = []
    
    for filename in chapter_files:
        filepath = chapters_dir / filename
        if not filepath.exists():
            print(f"Warning: {filepath} does not exist!")
            continue
        with open(filepath, "r", encoding="utf-8") as f:
            contents.append(f.read())
            
    # Compile a Table of Contents
    toc_lines = []
    toc_lines.append("# Table of Contents")
    toc_lines.append("")
    
    for part_idx, content in enumerate(contents):
        # Scan lines for markdown headers
        for line in content.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                if title == "Motionly Technical & Product Documentation":
                    continue
                # Link format
                anchor = title.lower().replace(" ", "-").replace("—", "").replace("&", "").replace("/", "").replace("(", "").replace(")", "").replace(".", "")
                anchor = "".join(c for c in anchor if c.isalnum() or c in "-_")
                toc_lines.append(f"- [{title}](#{anchor})")
            elif line.startswith("## "):
                title = line[3:].strip()
                anchor = title.lower().replace(" ", "-").replace("—", "").replace("&", "").replace("/", "").replace("(", "").replace(")", "").replace(".", "")
                anchor = "".join(c for c in anchor if c.isalnum() or c in "-_")
                toc_lines.append(f"  - [{title}](#{anchor})")
                
    toc_text = "\n".join(toc_lines)
    
    # Merge everything
    # part_1 starts with the title, let's split part_1 at the page-break
    part_1_content = contents[0]
    title_section, remainder = part_1_content.split('<div style="page-break-after: always;"></div>', 1)
    
    final_markdown = []
    final_markdown.append(title_section.strip())
    final_markdown.append('\n\n<div style="page-break-after: always;"></div>\n\n')
    final_markdown.append(toc_text)
    final_markdown.append('\n\n<div style="page-break-after: always;"></div>\n\n')
    final_markdown.append(remainder.strip())
    
    for i in range(1, len(contents)):
        final_markdown.append("\n\n" + contents[i].strip())
        
    final_text = "\n".join(final_markdown)
    
    # Write to workspace docs/
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(final_text)
    print(f"Successfully assembled and wrote documentation to {output_file}")
    
    # Write to artifacts dir
    artifact_dir.mkdir(parents=True, exist_ok=True)
    with open(artifact_file, "w", encoding="utf-8") as f:
        f.write(final_text)
    print(f"Successfully copied to artifact directory at {artifact_file}")

if __name__ == "__main__":
    assemble()
