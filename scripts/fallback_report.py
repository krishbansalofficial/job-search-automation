"""Create a portable report only when Google Sheets cannot be updated."""

import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def _latex_escape(value):
    replacements = {
        "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$",
        "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(char, char) for char in str(value))


def create_sheet_fallback_pdf(results, output_dir, error):
    """Return a report path, or None when the local PDF compiler fails."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    stem = f"job-search-sheet-fallback-{stamp}"
    tex_path = output / f"{stem}.tex"
    pdf_path = output / f"{stem}.pdf"

    rows = []
    for result in results:
        job = result["job"]
        score = result["score_result"]
        rows.append(
            "\\textbf{%s} --- %s\\\\\n"
            "Location: %s\\\\\nScore: %s\\\\\n"
            "URL: \\url{%s}\\\\[0.8em]\n"
            % (
                _latex_escape(job.get("company", "")),
                _latex_escape(job.get("title", "")),
                _latex_escape(job.get("location", "")),
                _latex_escape(score.get("match_score", "")),
                job.get("url", "").replace("%", r"\%"),
            )
        )

    tex_path.write_text(
        "\\documentclass[11pt]{article}\n"
        "\\usepackage[margin=0.75in]{geometry}\n"
        "\\usepackage[hidelinks]{hyperref}\n"
        "\\begin{document}\n"
        "\\section*{Job search report (Google Sheets fallback)}\n"
        "Google Sheets could not be updated: \\texttt{%s}\\par\n"
        "Generated: %s\\par\\bigskip\n%s\\end{document}\n"
        % (_latex_escape(error), stamp, "\n".join(rows))
    )
    try:
        result = subprocess.run(
            ["tectonic", str(tex_path), "--outdir", str(output)],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        print("  could not create Google Sheets fallback PDF: tectonic is not installed")
        return None
    if result.returncode != 0 or not pdf_path.exists():
        print("  could not create Google Sheets fallback PDF:")
        print(result.stderr[-2000:])
        return None
    return str(pdf_path)
