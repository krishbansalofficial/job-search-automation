"""
Compiles a LaTeX resume to PDF using tectonic, a free, dependency
free LaTeX engine that installs in seconds inside GitHub Actions.
No paid rendering API needed.

Install locally with:
  cargo install tectonic
or on the GitHub Actions runner, apt/conda per the workflow file.
"""

import re
import subprocess
from pathlib import Path


def apply_bullet_revisions(template_text, bullet_suggestions):
    """
    Swaps each 'original' bullet for its 'revised' version inside the
    LaTeX source. Falls back silently (keeps the original bullet) if
    an exact match isn't found, since LaTeX special characters can
    make fuzzy matching unreliable, and a missed swap is far better
    than a broken resume.
    """
    text = template_text
    for suggestion in bullet_suggestions:
        original = suggestion.get("original", "").strip()
        revised = suggestion.get("revised", "").strip()
        if original and revised and original in text:
            text = text.replace(original, revised, 1)
    return text


def compile_resume(template_path, bullet_suggestions, output_dir, filename_stem):
    template_text = Path(template_path).read_text()
    tailored_text = apply_bullet_revisions(template_text, bullet_suggestions)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    safe_stem = re.sub(r"[^a-zA-Z0-9_-]", "_", filename_stem)
    tex_path = output_dir / f"{safe_stem}.tex"
    tex_path.write_text(tailored_text)

    result = subprocess.run(
        ["tectonic", str(tex_path), "--outdir", str(output_dir)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  tectonic failed for {safe_stem}:")
        print(result.stderr[-2000:])
        return None

    pdf_path = output_dir / f"{safe_stem}.pdf"
    return str(pdf_path) if pdf_path.exists() else None
