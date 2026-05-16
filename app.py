"""Streamlit dashboard — последовательный нарратив для новичка-инвестора."""
from __future__ import annotations

import streamlit as st

from analyzer import edgar, xbrl, sections as sect_mod, upload as upload_mod, finviz, moat
from analyzer.config import ANTHROPIC_API_KEY as ENV_API_KEY
from analyzer.glossary import GLOSSARY
from analyzer.llm import build_narrative, Narrative
from analyzer.segments import latest_segments
from analyzer.viz import (
    waterfall_chart, history_chart, segment_pie, moat_bars, fmt_money,
)


st.set_page_config(page_title="Business Analyzer", page_icon="🔎", layout="wide")


def gloss(term: str) -> str:
    return GLOSSARY.get(term, "")


def card_title(emoji: str, title: str, subtitle: str = ""):
    st.markdown(f"### {emoji} {title}")
    if subtitle:
        st.caption(subtitle)


def traffic_light_html(color: str, label: str) -> str:
    colors = {"green": "#16a34a", "yellow": "#eab308", "red": "#dc2626"}
    bg = colors.get(color, "#9ca3af")
    return f"""
    <div style="background:{bg};color:white;padding:18px 24px;border-radius:12px;
                font-size:22px;font-weight:700;display:inline-block;">
        ● {label}
    </div>
    """


def bridge(text: str):
    if not text:
        return
    # $ заменяем на &#36; для безопасного рендеринга внутри HTML-блока
    safe = text.replace("$", "&#36;")
    st.markdown(
        f"<div style='border-left:3px solid #2563eb;padding:8px 16px;margin:12px 0;"
        f"background:#eff6ff;border-radius:0 8px 8px 0;color:#1e40af;font-style:italic;'>"
        f"🪜 {safe}</div>",
        unsafe_allow_html=True,
    )


def esc(text: str) -> str:
    """Экранирует $ чтобы Streamlit не интерпретировал их как LaTeX/MathJax."""
    if not text:
        return ""
    return text.replace("\\$", "$").replace("$", "\\$")


def safe_write(text: str):
    """st.write для LLM-текстов с экранированием $-знаков."""
    if not text:
        return
    st.markdown(esc(text))


# ─────────────────────────── HEADER ───────────────────────────────────────────

st.title("🔎 Business Analyzer")
st.caption("Загружай тикер или 10-K — получай дашборд про бизнес на языке новичка.")

# ── API key (Anthropic) ────────────────────────────────────────────────────
with st.expander("🔑 Anthropic API key" + (" — ✅ загружен из .env" if ENV_API_KEY else " — ⚠️ не задан"),
                 expanded=not bool(ENV_API_KEY)):
    st.markdown(
        "Получить ключ: [console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys). "
        "Пополнить баланс: [billing](https://console.anthropic.com/settings/billing). "
        "Один анализ ≈ 1-2 цента (Haiku 4.5)."
    )
    typed_key = st.text_input(
        "API key", value="", type="password",
        placeholder=("Ключ загружен из .env, перезаписать необязательно" if ENV_API_KEY else "sk-ant-..."),
        help="Сохраняется только в текущей сессии, не записывается на диск.",
    )
    if typed_key:
        st.session_state["api_key"] = typed_key
    elif ENV_API_KEY and "api_key" not in st.session_state:
        st.session_state["api_key"] = ENV_API_KEY

api_key = st.session_state.get("api_key", "")

col1, col2, col3 = st.columns([2, 3, 1])
with col1:
    ticker = st.text_input("Тикер", value="AAPL", help="Например AAPL, MSFT, KO").strip().upper()
with col2:
    uploaded = st.file_uploader("Или загрузи 10-K (PDF/HTML)", type=["pdf", "html", "htm"])
with col3:
    st.write("")
    st.write("")
    go = st.button("Разобрать компанию", type="primary", use_container_width=True)

if not api_key:
    st.error("❌ Anthropic API key не задан. Введи его в разделе «🔑 Anthropic API key» выше или добавь в `.env`.")
    st.stop()

if not go and "narrative" not in st.session_state:
    st.info("Введи тикер и нажми «Разобрать компанию». Первый запуск тикера займёт ~30 сек (загрузка отчётов + LLM-анализ).")
    st.stop()


# ─────────────────────────── DATA PIPELINE ────────────────────────────────────

@st.cache_data(show_spinner=False, ttl=3600)
def load_company(ticker: str, uploaded_name: str | None, uploaded_bytes: bytes | None):
    cik = edgar.ticker_to_cik(ticker) if ticker else None
    company = ""
    sic, sic_desc = "", ""
    annual = None
    waterfall = None
    metrics = {}
    segments_xbrl: dict[str, float] = {}

    if cik:
        company = edgar.company_name(cik)
        sic, sic_desc = edgar.sic_info(cik)
        annual = xbrl.build_financials(cik, "FY")
        waterfall = xbrl.latest_waterfall(annual)
        metrics = xbrl.compute_metrics(annual)
        segments_xbrl = latest_segments(cik)

    # Sections: всегда берём 10-K с EDGAR (там полное Item 1 Business + 1A Risks),
    # а если пользователь загрузил файл — поверх дозаполняем mdna из его файла (10-Q обычно).
    sections_text = {"business": "", "risks": "", "mdna": ""}
    if cik:
        filings = edgar.get_latest_filings(ticker)
        f10k = filings.get("10-K")
        if f10k:
            html = edgar.download_filing_html(f10k)
            sections_text = sect_mod.extract_sections(html)

    if uploaded_bytes:
        uploaded_sections = upload_mod.parse_uploaded(uploaded_name or "upload.html", uploaded_bytes)
        # Берём из загруженного то, что в нём есть (10-Q обычно имеет свежее MD&A + Risk updates)
        for key in ("business", "risks", "mdna"):
            if uploaded_sections.get(key):
                # Если 10-K уже дал business — оставляем 10-K (там описание продукта),
                # для risks/mdna предпочитаем загруженное (свежее)
                if key == "business" and sections_text.get("business"):
                    continue
                sections_text[key] = uploaded_sections[key]

    fv = finviz.parse_snapshot(finviz.fetch(ticker)) if ticker else {}
    return {
        "cik": cik,
        "company": company,
        "sic_desc": sic_desc,
        "annual": annual,
        "waterfall": waterfall,
        "metrics": metrics,
        "segments_xbrl": segments_xbrl,
        "sections": sections_text,
        "finviz": fv,
    }


with st.spinner("Скачиваю EDGAR + считаю финансы..."):
    data = load_company(
        ticker,
        uploaded.name if uploaded else None,
        uploaded.getvalue() if uploaded else None,
    )

if not data["cik"] and not uploaded:
    st.error(f"Тикер {ticker} не найден в EDGAR. Попробуй другой или загрузи 10-K вручную.")
    st.stop()


fv = data["finviz"]
sector = fv.get("sector") or ""
# Передаём в LLM пустой sector_comparison — sector-medians больше не строим
comparison: list[dict] = []


@st.cache_data(show_spinner=False, ttl=86400)
def gen_narrative(ticker, company, sector, metrics, multiples_summary, waterfall_dict,
                   history_summary, comp_summary, biz, risks, mdna, segments_txt, _api_key) -> Narrative:
    # _api_key prefix excludes it from Streamlit's cache hash to avoid caching by key
    return build_narrative(
        ticker=ticker, company_name=company, sector=sector,
        metrics=metrics, multiples=multiples_summary, waterfall=waterfall_dict,
        history_summary=history_summary, sector_comparison=comp_summary,
        business_text=biz, risks_text=risks, mdna_text=mdna,
        segments_text=segments_txt,
        api_key=_api_key,
    )


multiples_summary = {k: fv.get(k) for k in ("pe", "forward_pe", "ps", "pfcf", "ev_ebitda", "target_price", "recom", "price")}

history_summary = ""
if data["annual"] is not None and not data["annual"].empty:
    a = data["annual"]
    if "revenue" in a.columns:
        history_summary = f"Revenue 5y ago: {fmt_money(a['revenue'].iloc[-6])} → latest: {fmt_money(a['revenue'].iloc[-1])}"

waterfall_dict = data["waterfall"].__dict__ if data["waterfall"] else {}

try:
    with st.spinner("Генерирую нарратив через Claude..."):
        narr = gen_narrative(
            ticker, data["company"], sector,
            data["metrics"], multiples_summary, waterfall_dict,
            history_summary, comparison,
            data["sections"].get("business", ""),
            data["sections"].get("risks", ""),
            data["sections"].get("mdna", ""),
            # Передаём структурированную таблицу (если нашлась) + raw текст ноты
            (
                ("STRUCTURED TABLE (use these exact numbers):\n" + data["sections"]["segments_tables"] + "\n\n---\n\n")
                if data["sections"].get("segments_tables") else ""
            ) + "RAW NOTE TEXT (fallback):\n" + data["sections"].get("segments", ""),
            api_key,
        )
except Exception as e:
    st.error(f"LLM-вызов не удался: {e}. Проверь ANTHROPIC_API_KEY в .env.")
    narr = None


# ─────────────────────────── 1. INTRO ─────────────────────────────────────────

with st.container(border=True):
    card_title("👋", f"Знакомимся: {data['company'] or ticker}",
               "Кто это вообще и какой он по размеру")

    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Market Cap", fmt_money(fv.get("market_cap")), help=gloss("Market Cap"))
    with c2:
        st.metric("Сектор", sector or data["sic_desc"] or "—", help=gloss("Sector"))
    with c3:
        price = fv.get("price")
        target = fv.get("target_price")
        upside = ((target - price) / price * 100) if (price and target) else None
        st.metric("Цена / Цель аналитиков",
                  f"${price:.2f} → ${target:.2f}" if price and target else "—",
                  f"{upside:+.1f}% upside" if upside is not None else None,
                  help=gloss("Target Price"))

    if narr:
        safe_write(narr.intro.body)
        st.success(f"**В одной фразе:** {esc(narr.one_liner)}")
        bridge(narr.intro.bridge_next)


# ─────────────────────────── 2. REVENUE BREAKDOWN ─────────────────────────────

with st.container(border=True):
    card_title("🏪", "Чем зарабатывают?",
               "Что компания продаёт и в каких долях")

    seg_left, seg_right = st.columns([3, 2])
    with seg_left:
        seg_data = {}
        if narr and narr.segments:
            seg_data = {s.name: s.revenue_share_pct for s in narr.segments}
        elif data["segments_xbrl"]:
            seg_data = data["segments_xbrl"]
        fig = segment_pie(seg_data)
        if fig:
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Сегментная разбивка не извлечена.")

    with seg_right:
        if narr and narr.segments:
            # Если доли все нулевые — Claude не нашёл цифр в 10-K, показываем сегменты без %
            non_zero = [s for s in narr.segments if s.revenue_share_pct > 0]
            display_segs = non_zero if non_zero else narr.segments
            show_pct = bool(non_zero)
            for s in display_segs[:5]:
                if show_pct:
                    st.markdown(f"**{esc(s.name)}** — {s.revenue_share_pct:.0f}%  \n{esc(s.one_liner)}")
                else:
                    st.markdown(f"**{esc(s.name)}**  \n{esc(s.one_liner)}")
            if not show_pct:
                st.caption("ℹ️ Конкретные доли сегментов в 10-K не раскрыты — показаны только направления.")

    if narr:
        safe_write(narr.revenue.body)
        bridge(narr.revenue.bridge_next)


# ─────────────────────────── 3. WATERFALL ─────────────────────────────────────

with st.container(border=True):
    card_title("💰", "Путь $1 выручки → прибыль",
               "Сколько центов с каждого доллара продаж реально остаётся")

    if data["waterfall"]:
        fig = waterfall_chart(data["waterfall"])
        st.plotly_chart(fig, use_container_width=True)

        m = data["metrics"]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Валовая маржа", f"{m.get('gm', 0)*100:.0f}%" if m.get("gm") else "—", help=gloss("GM"))
        c2.metric("Операц. маржа", f"{m.get('om', 0)*100:.0f}%" if m.get("om") else "—", help=gloss("OM"))
        c3.metric("Чистая маржа", f"{m.get('nm', 0)*100:.0f}%" if m.get("nm") else "—", help=gloss("Net Income"))
        c4.metric("FCF маржа", f"{m.get('fcf_margin', 0)*100:.0f}%" if m.get("fcf_margin") else "—", help=gloss("FCF"))
    else:
        st.warning("Не удалось построить waterfall — нет финансовых данных.")

    if narr:
        safe_write(narr.waterfall.body)
        bridge(narr.waterfall.bridge_next)


# ─────────────────────────── 4. HISTORY ───────────────────────────────────────

with st.container(border=True):
    card_title("📈", "А стабильно ли так было — 10 лет истории?",
               "Растёт или стагнирует, маржа держится или размывается")

    if data["annual"] is not None and not data["annual"].empty:
        fig = history_chart(data["annual"])
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Исторические данные отсутствуют.")

    if narr:
        safe_write(narr.history.body)
        bridge(narr.history.bridge_next)


# ─────────────────────────── 5. MOAT ──────────────────────────────────────────

with st.container(border=True):
    card_title("🏰", "Почему конкуренты не отнимают эту прибыль?",
               "Сила бизнеса: ключевые метрики, тип «рва», конкуренты")

    # Метрики компании — простая таблица плашек, без сравнения с сектором
    m = data["metrics"]
    mcols = st.columns(5)
    metric_defs = [
        ("Валовая маржа", m.get("gm"), "GM"),
        ("Операц. маржа", m.get("om"), "OM"),
        ("ROIC", m.get("roic"), "ROIC"),
        ("Рост выручки 5Y", m.get("rev_cagr_5y"), "Revenue CAGR"),
        ("FCF маржа", m.get("fcf_margin"), "FCF"),
    ]
    for col, (label, val, gkey) in zip(mcols, metric_defs):
        with col:
            st.metric(label, f"{val*100:.1f}%" if val is not None else "—", help=gloss(gkey))

    st.markdown("**Типы рва (Morningstar):**", help=gloss("Moat"))
    if narr and narr.moat_types:
        emoji_map = {
            "intangibles": "🏷️ Бренд / патенты",
            "switching_costs": "🔒 Высокие издержки переключения",
            "network_effects": "🌐 Сетевой эффект",
            "cost_advantage": "⚙️ Преимущество в издержках",
            "efficient_scale": "📏 Эффективный масштаб",
        }
        for mt in narr.moat_types:
            mark = "✅" if mt.present else "❌"
            label = emoji_map.get(mt.type, mt.type)
            with st.expander(f"{mark} {label}", expanded=mt.present):
                if mt.evidence:
                    st.markdown(f"_«{esc(mt.evidence)}»_")
                else:
                    st.caption("Прямых свидетельств не найдено.")

    # Доли продуктовых рынков и конкуренты (LLM из 10-K Item 1)
    if narr and narr.market_positions:
        st.markdown("**Доли в продуктовых рынках (из 10-K Item 1):**")
        for mp in narr.market_positions:
            share_txt = f"**{mp.company_share_pct:.0f}%**" if mp.company_share_pct else "—"
            rank_txt = f" · #{mp.rank}" if mp.rank else ""
            with st.expander(f"📊 {esc(mp.market_name)} — {share_txt}{rank_txt}", expanded=False):
                st.markdown(f"_«{esc(mp.evidence)}»_")

    if narr and narr.competitors:
        st.markdown("**Главные конкуренты (из секции Competition):**")
        cols = st.columns(min(len(narr.competitors), 3))
        for i, comp in enumerate(narr.competitors[:6]):
            with cols[i % len(cols)]:
                st.markdown(f"🥊 **{esc(comp.name)}**  \n_{esc(comp.note)}_")

    if narr:
        safe_write(narr.moat.body)
        bridge(narr.moat.bridge_next)


# ─────────────────────────── 6. VALUATION ─────────────────────────────────────

with st.container(border=True):
    card_title("💵", "Дорого или дёшево покупаем?",
               "Мультипликаторы и мнение аналитиков")

    c1, c2, c3, c4 = st.columns(4)
    def metric_card(col, label, val, help_key, fmt=lambda x: f"{x:.1f}"):
        with col:
            st.metric(label, fmt(val) if val is not None else "—", help=gloss(help_key))
    metric_card(c1, "P/E", fv.get("pe"), "P/E")
    metric_card(c2, "P/S", fv.get("ps"), "P/S")
    metric_card(c3, "EV/EBITDA", fv.get("ev_ebitda"), "EV/EBITDA")
    metric_card(c4, "P/FCF", fv.get("pfcf"), "P/FCF")

    st.markdown("---")
    c1, c2, c3 = st.columns(3)
    price, target = fv.get("price"), fv.get("target_price")
    upside = ((target - price) / price * 100) if (price and target) else None
    c1.metric("Цена сейчас", f"${price:.2f}" if price else "—")
    c2.metric("Цель аналитиков", f"${target:.2f}" if target else "—",
              f"{upside:+.1f}%" if upside is not None else None)
    c3.metric("Рекомендация", finviz.recom_label(fv.get("recom")),
              help="Среднее аналитиков: 1=Strong Buy ... 5=Strong Sell")

    if narr:
        safe_write(narr.valuation.body)
        bridge(narr.valuation.bridge_next)


# ─────────────────────────── 7. RISKS ─────────────────────────────────────────

with st.container(border=True):
    card_title("⚠️", "Что может всё сломать?",
               "Главные риски из Item 1A — на человеческом языке")

    if narr and narr.risks:
        for r in narr.risks:
            icon = "🔴" if r.severity == "high" else "🟡"
            st.markdown(f"{icon} **{esc(r.title)}** — {esc(r.explanation)}")
    else:
        st.info("Риски не сгенерированы.")

    if narr:
        safe_write(narr.risks_block.body)
        bridge(narr.risks_block.bridge_next)


# ─────────────────────────── 8. OUTLOOK ───────────────────────────────────────

with st.container(border=True):
    card_title("🗣️", "Что говорит руководство?",
               "Выжимка из MD&A — что улучшилось, ухудшилось, планы")

    if narr and narr.outlook_bullets:
        for b in narr.outlook_bullets:
            icon = {"positive": "✅", "negative": "🔻", "plan": "🎯"}.get(b.label, "•")
            st.markdown(f"{icon} {esc(b.text)}")

    if narr:
        safe_write(narr.outlook.body)
        bridge(narr.outlook.bridge_next)


# ─────────────────────────── 9. VERDICT ───────────────────────────────────────

with st.container(border=True):
    card_title("🎯", "Итог", "Собираем всё в один вывод")
    if narr:
        labels = {"green": "Качественный бизнес", "yellow": "Со звёздочкой", "red": "Слабый / рискованный"}
        st.markdown(
            traffic_light_html(narr.traffic_light, labels.get(narr.traffic_light, "")),
            unsafe_allow_html=True,
        )
        st.write("")
        safe_write(narr.verdict)


# ─────────────────────────── FOOTER ───────────────────────────────────────────

with st.expander("📚 Глоссарий — все термины простыми словами"):
    for term, defn in GLOSSARY.items():
        # Экранируем $ чтобы Streamlit не интерпретировал как LaTeX
        safe = defn.replace("$", "\\$")
        st.markdown(f"**{term}** — {safe}")

st.caption(f"Источник: SEC EDGAR · finviz · Anthropic Claude. Тикер: {ticker} · CIK: {data['cik']}")
