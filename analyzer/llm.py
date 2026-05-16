"""Anthropic Claude narrative generator — one tool-use call returning the whole story.

Uses tool_use to force structured output matching the Narrative Pydantic schema,
and prompt caching to make repeated runs on the same 10-K cheap.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL, CACHE_DIR


class NarrativeBlock(BaseModel):
    body: str = Field(default="", description="2-3 предложения текста внутри карточки на русском, без жаргона")
    bridge_next: str = Field(default="", description="1 предложение-мостик в следующую карточку, ссылается на конкретное число")


class MoatTypeEntry(BaseModel):
    type: Literal["intangibles", "switching_costs", "network_effects", "cost_advantage", "efficient_scale"]
    present: bool
    evidence: str = Field(description="Цитата из 10-K, не более 2 предложений; пустая если present=false")


class RiskEntry(BaseModel):
    title: str = Field(description="Короткий заголовок риска (5-8 слов)")
    explanation: str = Field(description="Объяснение в формате 'если X — то Y', без жаргона")
    severity: Literal["medium", "high"] = "medium"


class SegmentEntry(BaseModel):
    name: str = Field(description="Название продукта или сегмента (например 'iPhone', 'Services')")
    revenue_share_pct: float = Field(description="Доля от выручки в %, число 0-100")
    one_liner: str = Field(description="Одно предложение что это и кому продают")


class OutlookBullet(BaseModel):
    label: Literal["positive", "negative", "plan"]
    text: str = Field(description="Одно предложение из MD&A: что улучшилось/ухудшилось/план")


class MarketPositionEntry(BaseModel):
    market_name: str = Field(description="Конкретный продуктовый рынок, например 'US soft drinks', 'Смартфоны в США', 'Глобальный рынок premium спорткаров'")
    company_share_pct: float = Field(default=0, description="Доля компании на этом рынке в %. 0 если в 10-K не указано конкретное число.")
    rank: int = Field(default=0, description="Позиция компании на рынке (1, 2, 3). 0 если не указано.")
    evidence: str = Field(description="Цитата из 10-K Item 1, подтверждающая позицию. Не более 2 предложений.")


class CompetitorEntry(BaseModel):
    name: str = Field(description="Название конкурента, например 'PepsiCo'")
    note: str = Field(description="Одно предложение: на каком рынке/в чём конкурирует")


class Narrative(BaseModel):
    intro: NarrativeBlock = Field(default_factory=NarrativeBlock)
    revenue: NarrativeBlock = Field(default_factory=NarrativeBlock)
    waterfall: NarrativeBlock = Field(default_factory=NarrativeBlock)
    history: NarrativeBlock = Field(default_factory=NarrativeBlock)
    moat: NarrativeBlock = Field(default_factory=NarrativeBlock)
    valuation: NarrativeBlock = Field(default_factory=NarrativeBlock)
    risks_block: NarrativeBlock = Field(default_factory=NarrativeBlock)
    outlook: NarrativeBlock = Field(default_factory=NarrativeBlock)
    verdict: str = Field(default="", description="2-3 абзаца финального вывода для новичка")
    traffic_light: Literal["green", "yellow", "red"] = Field(default="yellow")
    moat_types: list[MoatTypeEntry] = Field(default_factory=list)
    risks: list[RiskEntry] = Field(default_factory=list, description="5-7 главных рисков перефразированных простым языком")
    segments: list[SegmentEntry] = Field(default_factory=list, description="3-6 основных сегментов выручки с долями, сумма ~100%")
    outlook_bullets: list[OutlookBullet] = Field(default_factory=list, description="3-5 буллетов из MD&A")
    market_positions: list[MarketPositionEntry] = Field(default_factory=list, description="2-4 ключевых продуктовых рынка где компания занимает значимую долю, с цитатами из 10-K. Если в тексте нет конкретных долей — оставь массив пустым.")
    competitors: list[CompetitorEntry] = Field(default_factory=list, description="3-5 главных конкурентов, упомянутых в Item 1, с пояснением где именно конкурируют")
    one_liner: str = Field(default="", description="Одна фраза, объясняющая чем компания зарабатывает, как ребёнку")


SYSTEM_PROMPT = """Ты помогаешь новичку, который первый раз слышит про инвестиции, разобраться в компании за 5 минут.

ПРАВИЛА:
1. Объясняй на русском, как другу. Без жаргона. Если используешь термин (P/E, ROIC, маржа) — рядом в скобках объяснение в 5-7 слов.
2. Каждый блок body — 2-3 коротких предложения, конкретно про эту компанию (ссылайся на числа из контекста).
3. Поле bridge_next — это мостик в следующую карточку, должен ссылаться на конкретное число из текущего блока и логически вводить следующую тему. Пример: "Видим, что маржа стабильна 30% десять лет — значит, у бизнеса есть что-то, что защищает прибыль. Дальше — что именно."
4. traffic_light: green = качественный бизнес по разумной цене, yellow = есть оговорки, red = слабый/рискованный/переоценённый.
5. Для рисков — перефразируй из Item 1A в формат "если X произойдёт, то прибыль/выручка пострадает", не копируй дословно.
6. Для типов рва — отметь true только если в тексте есть прямое свидетельство, и приведи цитату.
7. Возвращай результат ТОЛЬКО через инструмент emit_narrative — никакого свободного текста.
8. Не путай термины: «target price» от аналитиков — это **прогнозируемая цена на 12 месяцев** (то есть «куда они ждут роста»), а не «дешевеет». Если target > price — «аналитики ждут роста», если target < price — «аналитики ждут падения».
9. КРИТИЧНО — анти-галлюцинации: НИКОГДА не выдумывай конкретные числа. Цифры можно использовать ТОЛЬКО двух типов:
   (а) числа из STRUCTURED FACTS (revenue, margins, multiples и т.д. — они проверены)
   (б) числа, **дословно встречающиеся** в переданном тексте 10-K Item 1/1A/MD&A
   Если хочешь сказать «60% выручки из-за рубежа», «компания списала 960 млн на BodyArmor», «доля рынка 43%» — найди это в тексте. Если не нашёл — НЕ ПИШИ ЦИФРУ. Используй формулировки: «значительная часть», «существенный сегмент», «компания делала списания в прошлом» — без чисел.
   Это правило приоритетнее остальных. Лучше пустое поле, чем выдуманное число.
10. segments — КРИТИЧНО. Приоритеты источников:
    (a) Если в блоке "Operating Segments Note" есть таблица с цифрами выручки по сегментам (Net operating revenues по строкам типа "EMEA / Latin America / North America / Asia Pacific / Bottling Investments" или по продуктовым линиям) — ИСПОЛЬЗУЙ ЭТУ ТАБЛИЦУ. Возьми самый свежий год (обычно крайний правый столбец, или первый по дате). Посчитай долю каждой строки от total/consolidated. Заполни name (точно как в таблице) и revenue_share_pct (число 0-100). Это твой PRIMARY источник долей.
    (b) Если в Item 1 явно указаны % разбивки по продуктам/географии («Sparkling soft drinks accounted for 69% of our revenue») — используй их.
    (c) Если ни (a) ни (b) — верни сегменты с revenue_share_pct=0 (UI покажет как «направления»).
    НЕ ИГНОРИРУЙ таблицу из (a) только потому что разбивка по географии, а не по продукту — географическая разбивка тоже валидна и часто единственная доступная.
11. market_positions: ТОЛЬКО при наличии в Item 1 прямого утверждения с числом («we hold 43%», «#2 with 18% share»). Иначе пустой массив. evidence — точная цитата.
12. competitors: имена ТОЛЬКО из секции Competition в Item 1. Не из общих знаний. Если секции нет — пустой массив."""


def _ckey(*parts: str) -> str:
    h = hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]
    return h


def _cache_path(ticker: str, key: str) -> Path:
    p = CACHE_DIR / "llm" / ticker.upper() / f"{key}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _truncate(s: str, n: int) -> str:
    return s[:n] if s else ""


def _strip_metadata(schema: dict) -> dict:
    """Remove $defs and resolve $ref so Anthropic tool input_schema accepts it."""
    defs = schema.pop("$defs", None) or schema.pop("definitions", None) or {}

    def resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                ref = node["$ref"].split("/")[-1]
                resolved = defs.get(ref, {})
                return resolve({k: v for k, v in resolved.items()})
            return {k: resolve(v) for k, v in node.items() if k not in ("title", "default")}
        if isinstance(node, list):
            return [resolve(x) for x in node]
        return node

    return resolve(schema)


# Полный список топ-уровневых полей Narrative — принудительно отмечаем их required
# в JSON-схеме, чтобы Claude старался заполнить все, даже если Pydantic-модель имеет defaults.
NARRATIVE_REQUIRED = [
    "intro", "revenue", "waterfall", "history", "moat", "valuation",
    "risks_block", "outlook", "verdict", "traffic_light",
    "moat_types", "risks", "segments", "outlook_bullets",
    "market_positions", "competitors", "one_liner",
]


def build_narrative(
    ticker: str,
    company_name: str,
    sector: str,
    metrics: dict,
    multiples: dict,
    waterfall: dict,
    history_summary: str,
    sector_comparison: list[dict],
    business_text: str,
    risks_text: str,
    mdna_text: str,
    segments_text: str = "",
    use_cache: bool = True,
    api_key: str | None = None,
) -> Narrative:
    cache_key = _ckey(ticker, str(metrics.get("gm", "")), business_text[:500], risks_text[:500])
    cpath = _cache_path(ticker, cache_key)
    if use_cache and cpath.exists():
        return Narrative.model_validate_json(cpath.read_text())

    key = api_key or ANTHROPIC_API_KEY
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not provided (set in .env or pass via UI)")

    structured_facts = {
        "ticker": ticker,
        "company_name": company_name,
        "sector": sector,
        "key_metrics": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in metrics.items()},
        "multiples": {k: v for k, v in multiples.items() if k != "raw"},
        "waterfall_latest": waterfall,
        "history_summary": history_summary,
        "sector_comparison": sector_comparison,
    }

    # segments_text может содержать два блока, разделённых маркером ---STRUCTURED---
    long_text = (
        f"=== 10-K Item 1 (Business) ===\n{_truncate(business_text, 18000)}\n\n"
        f"=== 10-K Item 1A (Risk Factors) ===\n{_truncate(risks_text, 18000)}\n\n"
        f"=== 10-K Item 7 (MD&A) ===\n{_truncate(mdna_text, 12000)}\n\n"
        f"=== 10-K Operating Segments (structured + raw) ===\n{_truncate(segments_text, 14000)}"
    )

    schema = _strip_metadata(Narrative.model_json_schema())
    schema["required"] = NARRATIVE_REQUIRED

    from anthropic import Anthropic
    client = Anthropic(api_key=key)

    resp = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=16000,
        system=[
            {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
        ],
        tools=[{
            "name": "emit_narrative",
            "description": "Emit the full structured narrative for the company dashboard.",
            "input_schema": schema,
        }],
        tool_choice={"type": "tool", "name": "emit_narrative"},
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "STRUCTURED FACTS:\n" + json.dumps(structured_facts, ensure_ascii=False, indent=2),
                    },
                    {
                        "type": "text",
                        "text": long_text,
                        "cache_control": {"type": "ephemeral"},
                    },
                    {
                        "type": "text",
                        "text": "Сгенерируй Narrative через инструмент emit_narrative.",
                    },
                ],
            }
        ],
    )

    tool_input = None
    for block in resp.content:
        if block.type == "tool_use" and block.name == "emit_narrative":
            tool_input = block.input
            break
    if tool_input is None:
        raise RuntimeError(f"Claude did not return tool_use. Stop reason: {resp.stop_reason}")

    # Pydantic с дефолтами сам заполнит пропущенные поля
    parsed = Narrative.model_validate(tool_input)
    if resp.stop_reason == "max_tokens" and not parsed.verdict:
        parsed.verdict = "⚠️ Вывод не сгенерирован полностью (LLM упёрся в лимит токенов). Перезапусти анализ."
    cpath.write_text(parsed.model_dump_json(indent=2))
    return parsed
