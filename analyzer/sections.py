"""Extract key sections from 10-K HTML: Item 1 (Business), 1A (Risk Factors), 7 (MD&A)."""
from __future__ import annotations

import re
from typing import Optional

import warnings
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# Headings we want, and the regex catching the section start.
# These match the actual section headers (e.g. "Item 1. Business") and avoid
# cross-references like "Item 1 of this Form 10-K under the heading 'Business'".
SECTION_PATTERNS: dict[str, list[re.Pattern]] = {
    "business": [
        re.compile(r"\bItem\s+1\.?\s*[\-—\s]*\s*Business\b", re.IGNORECASE),
    ],
    "risks": [
        re.compile(r"\bItem\s+1A\.?\s*[\-—\s]*\s*Risk\s+Factors\b", re.IGNORECASE),
    ],
    "mdna": [
        re.compile(r"\bItem\s+7\.?\s*[\-—\s]*\s*Management['’]s\s+Discussion", re.IGNORECASE),
    ],
    "segments": [
        # "NOTE NN: OPERATING SEGMENTS" / "NET OPERATING REVENUES" — заголовок ноты, может быть разделён переносами
        re.compile(r"\bNOTE\s+\d+\s*[:.]\s*\n?\s*(OPERATING\s+SEGMENTS|SEGMENT\s+INFORMATION|SEGMENT\s+REPORTING|REPORTABLE\s+SEGMENTS|NET\s+OPERATING\s+REVENUES|REVENUE\s+RECOGNITION|DISAGGREGATION\s+OF\s+REVENUE)\b", re.IGNORECASE),
    ],
}

# Heuristics for the *end* of each section
END_PATTERNS: dict[str, list[re.Pattern]] = {
    "business": [
        re.compile(r"\bItem\s+1A\.?\s*[\-—\s]*\s*Risk\s+Factors\b", re.IGNORECASE),
    ],
    "risks": [
        re.compile(r"\bItem\s+1B\.?", re.IGNORECASE),
        re.compile(r"\bItem\s+2\.?\s*[\-—\s]*\s*Properties\b", re.IGNORECASE),
    ],
    "mdna": [
        re.compile(r"\bItem\s+7A\.?", re.IGNORECASE),
        re.compile(r"\bItem\s+8\.?\s*[\-—\s]*\s*Financial\s+Statements\b", re.IGNORECASE),
    ],
    "segments": [
        # Заканчивается на следующем NOTE с заголовком в верхнем регистре — а не на in-text refs типа "Note 11"
        re.compile(r"(?:^|\n)\s*NOTE\s+\d+\s*[:.]\s*\n?\s*[A-Z][A-Z\s,&\-]{4,}", ),
    ],
}


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text("\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def _find_section(text: str, start_patterns: list[re.Pattern], end_patterns: list[re.Pattern],
                  max_chars: int = 60000) -> str:
    # Find LAST occurrence of any start pattern (TOC has earlier ones)
    starts = []
    for p in start_patterns:
        starts.extend(m.start() for m in p.finditer(text))
    if not starts:
        return ""
    start = max(starts)
    # End = first occurrence of any end pattern after start (минимум +500 чтобы не схватить сам же заголовок)
    ends = []
    for p in end_patterns:
        for m in p.finditer(text):
            if m.start() > start + 500:
                ends.append(m.start())
                break
    end = min(ends) if ends else min(start + max_chars, len(text))
    return text[start:end].strip()[:max_chars]


def extract_sections(html: str) -> dict[str, str]:
    text = html_to_text(html)
    out = {}
    for key, starts in SECTION_PATTERNS.items():
        # Сегментная нота — короткая (15К), остальные секции до 60К
        max_chars = 15000 if key == "segments" else 60000
        out[key] = _find_section(text, starts, END_PATTERNS[key], max_chars=max_chars)
    # Дополнительно — структурированные таблицы сегментной выручки из HTML
    out["segments_tables"] = extract_segments_tables(html)
    return out


def extract_segments_tables(html: str) -> str:
    """Парсит сегментную таблицу из 10-K HTML и возвращает структурированный текст:

        Operating Segments revenue (latest fiscal year, USD millions):
        - EMEA: 10833
        - Latin America: 6331
        ...
        Total: 47941

    Этот формат Claude парсит идеально и не путается с merged cells.
    """
    try:
        import pandas as pd
        from io import StringIO
        tables = pd.read_html(StringIO(html))
    except Exception:
        return ""

    for df in tables:
        if df.empty or df.shape[0] < 4 or df.shape[1] < 5:
            continue
        # Строим плоский text-snapshot всей таблицы
        flat_cells = [str(c).strip() for c in df.values.flatten() if str(c).strip() not in ("nan", "")]
        flat_lower = " | ".join(flat_cells).lower()
        # Подходящие таблицы: содержат имена сегментов + строку Third party + $ суммы
        # Гео-сегменты ИЛИ продуктовые сегменты типа AAPL
        has_geo = any(s in flat_lower for s in (
            "emea", "north america", "asia pacific", "americas", "greater china", "europe"))
        has_product = any(s in flat_lower for s in (
            "iphone", "mac", "ipad", "wearables", "services", "azure", "search advertising"))
        has_data_row = any(s in flat_lower for s in (
            "third party", "net sales", "total net sales", "total revenue", "net operating revenue"))
        if not ((has_geo or has_product) and has_data_row):
            continue

        # Находим header row с именами сегментов и data row "Third party"
        rows = df.fillna("").astype(str).values.tolist()
        # Header row: содержит >= 3 уникальных segment names
        SEGMENT_HINTS = [
            # Geographic
            "EMEA", "Latin America", "North America", "Asia Pacific",
            "Americas", "Europe", "Greater China", "Japan", "Rest of Asia Pacific",
            # KO-specific
            "Bottling Investments", "Global Ventures",
            # Product (Apple)
            "iPhone", "Mac", "iPad", "Wearables, Home and Accessories",
            "Wearables Home and Accessories", "Services",
            # Aggregates (separated below)
            "Corporate", "Consolidated", "Eliminations",
            "Operating Segments Total", "Total Operating Segments",
            "Total Reportable Segments", "Total",
        ]
        header_idx = None
        for i, row in enumerate(rows):
            hits = sum(1 for cell in row for hint in SEGMENT_HINTS if hint.lower() in cell.lower())
            if hits >= 3:
                header_idx = i
                break
        if header_idx is None:
            continue

        # Data row: предпочитаем "Third party" (KO) и "Net sales" (AAPL).
        # Пропускаем строки-подзаголовки (заканчиваются ":") и строки без чисел.
        DATA_ROW_PREFERENCES = [
            ("third party",),
            ("net sales", "total net sales"),
            ("total net operating revenues",),
            ("total revenue", "revenue"),
        ]

        def find_row_with_label(labels: tuple) -> int | None:
            for i, row in enumerate(rows[header_idx + 1:], start=header_idx + 1):
                # Должны быть числовые ячейки
                has_number = any(any(ch.isdigit() for ch in c.replace(",", "")) for c in row)
                if not has_number:
                    continue
                for cell in row:
                    low = cell.lower().strip().rstrip(":")
                    if low in labels:
                        return i
            return None

        tp_idx = None
        for pref in DATA_ROW_PREFERENCES:
            tp_idx = find_row_with_label(pref)
            if tp_idx is not None:
                break
        if tp_idx is None:
            continue

        header = rows[header_idx]
        data = rows[tp_idx]

        # Маппим: для каждого segment-имени берём ПЕРВОЕ числовое значение в той же или следующей колонке
        result: dict[str, float] = {}
        for col_idx, cell in enumerate(header):
            cell_clean = cell.strip()
            matched_segment = None
            for hint in SEGMENT_HINTS:
                if cell_clean.lower() == hint.lower():
                    matched_segment = hint
                    break
            if not matched_segment or matched_segment in result:
                continue
            # Ищем число в той же или соседних колонках data row
            for off in range(0, min(4, len(data) - col_idx)):
                val_cell = data[col_idx + off].replace(",", "").replace("$", "").strip()
                try:
                    val = float(val_cell)
                    if val > 100:  # фильтр маленьких артефактов
                        result[matched_segment] = val
                        break
                except ValueError:
                    continue

        if len(result) < 3:
            continue

        # Форматируем — отделяем агрегаты от настоящих сегментов
        AGGREGATE_KEYS = ("Consolidated", "Operating Segments Total",
                          "Total Operating Segments", "Total Reportable Segments",
                          "Total", "Eliminations")
        total = None
        for k in AGGREGATE_KEYS:
            v = result.pop(k, None)
            if v and (total is None or v > total):
                total = v
        corporate = result.pop("Corporate", None)

        lines = ["Operating Segments revenue (latest fiscal year, USD millions):"]
        for name, val in sorted(result.items(), key=lambda x: -x[1]):
            lines.append(f"- {name}: {val:,.0f}")
        if corporate:
            lines.append(f"- Corporate: {corporate:,.0f}")
        if total:
            lines.append(f"Total: {total:,.0f}")
        return "\n".join(lines)

    return ""


def extract_from_pdf(pdf_path: str) -> dict[str, str]:
    """Manual upload fallback — extract text from PDF then run section finder."""
    import pdfplumber
    text_chunks = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text_chunks.append(page.extract_text() or "")
    text = "\n".join(text_chunks)
    # Synthesize minimal HTML to reuse the same logic
    fake_html = f"<html><body><pre>{text}</pre></body></html>"
    return extract_sections(fake_html)


if __name__ == "__main__":
    import sys
    from .edgar import get_latest_filings, download_filing_html
    t = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    f = get_latest_filings(t)["10-K"]
    html = download_filing_html(f)
    s = extract_sections(html)
    for k, v in s.items():
        print(f"\n=== {k} ({len(v)} chars) ===")
        print(v[:600], "...\n")
