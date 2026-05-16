"""Manual upload path — parse user-provided PDF or HTML into the same sections dict."""
from __future__ import annotations

import io
from pathlib import Path

from .sections import extract_sections, extract_from_pdf


def parse_uploaded(name: str, data: bytes) -> dict[str, str]:
    """Detect type by filename, return sections dict."""
    suffix = Path(name).suffix.lower()
    if suffix == ".pdf":
        # pdfplumber needs a path or file-like
        import pdfplumber
        chunks = []
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages:
                chunks.append(page.extract_text() or "")
        text = "\n".join(chunks)
        fake_html = f"<html><body><pre>{text}</pre></body></html>"
        return extract_sections(fake_html)
    if suffix in (".html", ".htm", ".xml"):
        return extract_sections(data.decode("utf-8", errors="ignore"))
    return {"business": "", "risks": "", "mdna": ""}
