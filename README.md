# 🔎 Business Analyzer

> 🚀 **Live**: [business-analyzer-cheesecake.streamlit.app](https://business-analyzer-cheesecake.streamlit.app/?ticker=AAPL) · [Demo: AAPL](https://business-analyzer-cheesecake.streamlit.app/?ticker=AAPL) · [KO](https://business-analyzer-cheesecake.streamlit.app/?ticker=KO) · [NVDA](https://business-analyzer-cheesecake.streamlit.app/?ticker=NVDA)

[![Streamlit](https://img.shields.io/badge/Streamlit-live-FF4B4B?logo=streamlit&logoColor=white)](https://business-analyzer-cheesecake.streamlit.app/)
[![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Streamlit-дашборд, который по тикеру (или загруженному 10-K) собирает понятную **карточку компании на языке новичка**. Источники — SEC EDGAR (XBRL + текст отчётов) и finviz; нарратив генерируется через **Anthropic Claude Haiku 4.5**.

Цель: человек, который первый раз слышит слово «маржа», за 5 минут должен понять — что компания продаёт, зарабатывает или сжигает деньги, дорогая или дешёвая, какие основные риски, и стоит ли копать глубже.

## Попробовать

Открой [демо-ссылку](https://business-analyzer-cheesecake.streamlit.app/?ticker=AAPL) → введи свой [Anthropic API key](https://console.anthropic.com/settings/keys) (для full features) → готово. Один анализ ≈ 1-2 цента (~250 тикеров на $5).

Без ключа дашборд работает в режиме «только цифры» — waterfall, метрики, мультипликаторы, сегменты выручки. Без LLM-нарратива и рисков.

## Что показывает дашборд

Один тикер → 9 последовательных карточек, каждая отвечает на один вопрос:

1. **👋 Знакомимся** — кто компания, размер, цена / target аналитиков
2. **🏪 Чем зарабатывают** — pie сегментов выручки + описание
3. **💰 Путь $1 → прибыль** — Plotly waterfall: Revenue → COGS → GP → OpEx → OI → Tax → Net
4. **📈 10 лет истории** — выручка + динамика маржинальности
5. **🏰 Сила бизнеса** — GM / OM / ROIC / Rev CAGR / FCF margin + 5 типов рва Morningstar с цитатами из 10-K + конкуренты из секции Competition
6. **💵 Дорого или дёшево** — P/E, P/S, EV/EBITDA, P/FCF + target и Buy/Hold/Sell от finviz
7. **⚠️ Что может сломать** — 5-7 рисков из Item 1A, перефразированы как «если X — то Y»
8. **🗣️ Что говорит руководство** — выжимка из MD&A (что улучшилось/ухудшилось/планы)
9. **🎯 Итог** — светофор 🟢🟡🔴 + 2-3 абзаца вердикта

Все термины (P/E, ROIC, FCF…) — с tooltip-ом из глоссария. Между блоками — фразы-мостики, ссылающиеся на конкретные числа компании.

## Quick start (локально)

```bash
git clone https://github.com/akopyan757/business-analyzer.git
cd business-analyzer
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
# заполни ANTHROPIC_API_KEY и SEC_USER_AGENT (email — требование SEC)
.venv/bin/streamlit run app.py
```

Открывается на http://localhost:8501. Ввести тикер (AAPL / MSFT / KO / TSLA…) → «Разобрать компанию». Первый запуск тикера: ~30 сек (загрузка EDGAR + LLM ~$0.01). Последующие — мгновенно из кеша.

### Зависимости от API

- **Anthropic API key** — получить на [console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys). Один анализ ≈ 1-2 цента (Claude Haiku 4.5).
- **SEC user agent** — формат `Имя email`, требование SEC EDGAR для всех запросов.

Ключ можно ввести прямо в UI (раздел «🔑 Anthropic API key» в шапке) — сохраняется только в сессии, не пишется на диск.

### Публичный деплой (Streamlit Cloud)

Если деплоите как **публичный** app — **не** кладите свой `ANTHROPIC_API_KEY` в секреты Streamlit Cloud, иначе все посетители будут жечь ваш баланс. В секретах достаточно только `SEC_USER_AGENT`:

```toml
SEC_USER_AGENT = "Your Name your.email@example.com"
```

Каждый посетитель вводит свой Anthropic key через UI-поле в шапке. Ключ хранится только в сессии браузера, на сервер не пишется.

## Архитектура

```
business_analyzer/
├── app.py                    # Streamlit entry — UI и нарратив
├── requirements.txt
├── .env.example
├── README.md
└── analyzer/
    ├── config.py             # .env loader, headers
    ├── edgar.py              # SEC EDGAR: ticker→CIK, latest 10-K/10-Q, raw HTML
    ├── xbrl.py               # companyfacts JSON → financials DataFrame + 5 метрик
    ├── sections.py           # извлечение Item 1 / 1A / 7 + segment-таблицы из 10-K
    ├── upload.py             # fallback для ручной загрузки PDF/HTML
    ├── finviz.py             # scrape мультипликаторов и target price
    ├── macrotrends.py        # best-effort 10-летняя история (часто 0 из-за JS)
    ├── segments.py           # latest segment revenue из XBRL
    ├── moat.py               # placeholder (бенчмарки убраны)
    ├── llm.py                # Anthropic Claude tool-use → структурный Narrative
    ├── viz.py                # Plotly waterfall / history / pie / moat bars
    └── glossary.py           # словарь терминов для tooltip-ов
```

### Поток данных на один тикер

```
ticker → EDGAR (CIK + 10-K HTML + companyfacts XBRL)
       → finviz (multiples, target, Buy/Hold/Sell)
       → sections.extract_sections (Item 1/1A/7) + extract_segments_tables (NOTE 20)
       ↓
   llm.build_narrative (один Anthropic-вызов с prompt caching)
       ↓
   Narrative (Pydantic) → 9 секций Streamlit + Plotly виз
```

## Поддерживаемые типы компаний

Хорошо работает на:
- **Tech / Consumer** (AAPL, MSFT, GOOGL, KO, PG, WMT, COST) — все блоки + сегменты с долями
- **Pharma** (JNJ, PFE) — pretax-income как fallback для OperatingIncome
- **Industrials** (BA, XOM) — даже убыточные сценарии показывают красные маржи корректно
- **Communications / Media** (NFLX, DIS) — derive GP/OpEx когда тег устарел
- **REIT** (PLD) — частично, ROIC занижен из-за специфики учёта

Хуже работает на:
- **Банки** (JPM) — waterfall шаблон не подходит для финансовых (нет COGS, нужен Net Interest Income)
- **Insurance** (UNH) — структура IS отличается

## Что НЕ делает

- Не анализирует non-US тикеры (требуется 10-K, ADR/20-F не поддерживаются)
- Не покажет «настоящие» доли рынка (Statista / IBISWorld платные) — только то, что компания сама раскрывает в Item 1
- Не делает свой forecast — только consensus аналитиков из finviz
- Не сохраняет историю анализов (только кеш сырья)

## Лицензия

MIT
