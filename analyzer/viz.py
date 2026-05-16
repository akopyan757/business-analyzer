"""Plotly visualizations for the dashboard."""
from __future__ import annotations

from typing import Optional

import pandas as pd
import plotly.graph_objects as go

from .xbrl import WaterfallRow

GREEN = "#16a34a"
RED = "#dc2626"
YELLOW = "#eab308"
BLUE = "#2563eb"
GREY = "#9ca3af"


def fmt_money(v: float) -> str:
    if v is None or pd.isna(v):
        return "—"
    a = abs(v)
    if a >= 1e12:
        return f"${v/1e12:.2f}T"
    if a >= 1e9:
        return f"${v/1e9:.1f}B"
    if a >= 1e6:
        return f"${v/1e6:.1f}M"
    return f"${v:,.0f}"


def waterfall_chart(w: WaterfallRow) -> go.Figure:
    """Кастомный waterfall на go.Bar — синие столбцы (totals) стоят от 0 до значения,
    красные (subtractions) висят между предыдущим total и новым.
    """
    rev = w.revenue
    scale = 100.0 / rev if rev else 1.0

    # Каждый шаг: (название, base, height, is_total, raw_value_for_label)
    steps = [
        ("Выручка",            0,                            rev,                      True,  rev),
        ("− Себестоимость",    w.gross_profit,               w.cogs,                   False, -w.cogs),
        ("Валовая прибыль",    0,                            w.gross_profit,           True,  w.gross_profit),
        ("− Операц. расходы",  w.operating_income,           w.opex,                   False, -w.opex),
        ("Операц. прибыль",    0,                            w.operating_income,       True,  w.operating_income),
        ("− Налоги/проценты",  w.net_income,                 w.tax_and_interest,       False, -w.tax_and_interest),
        ("Чистая прибыль",     0,                            w.net_income,             True,  w.net_income),
    ]

    x_labels = [s[0] for s in steps]
    bases    = [s[1] for s in steps]
    heights  = [s[2] for s in steps]
    colors   = [BLUE if s[3] else RED for s in steps]

    text = []
    for name, base, height, is_total, raw in steps:
        if is_total:
            pct = (raw / rev * 100) if rev else 0
            text.append(f"<b>{fmt_money(raw)}</b><br>${raw*scale:.0f} из $100<br>({pct:.0f}%)")
        else:
            pct = (abs(raw) / rev * 100) if rev else 0
            text.append(f"{fmt_money(raw)}<br>{pct:.0f}% от выручки")

    fig = go.Figure()
    fig.add_bar(
        x=x_labels, y=heights, base=bases, marker_color=colors,
        text=text, textposition="outside", cliponaxis=False,
        textfont=dict(size=11),
        constraintext="none",
        hovertemplate="%{x}<br>%{text}<extra></extra>",
    )

    # Connector-линии: пунктир от верха текущего бара до верха следующего бара,
    # рисуем через shapes с xref="x" (категории) — каждая линия между i и i+1.
    shapes = []
    for i in range(len(steps) - 1):
        top_cur = bases[i] + heights[i]
        top_next = bases[i + 1] + heights[i + 1]
        shapes.append(dict(
            type="line", xref="x", yref="y",
            x0=i, x1=i + 1, y0=top_cur, y1=top_next,
            line=dict(color=GREY, width=1, dash="dot"),
        ))

    fig.update_layout(
        title=f"Путь $1 выручки → прибыль  ·  {w.period_end}",
        showlegend=False, margin=dict(t=60, l=40, r=60, b=60),
        height=500, plot_bgcolor="white", bargap=0.35,
        yaxis=dict(range=[0, rev * 1.22], showticklabels=False, showgrid=False, zeroline=True, zerolinecolor="#ddd"),
        xaxis=dict(showgrid=False, type="category", tickangle=0),
        shapes=shapes,
        uniformtext=dict(mode="show", minsize=10),
    )
    return fig


def history_chart(annual: pd.DataFrame) -> go.Figure:
    """Multi-line: Revenue (bars, secondary), GM%, OM%, FCF margin %."""
    if annual.empty:
        return go.Figure()
    df = annual.copy()
    df = df.tail(11)  # last ~10 years
    rev_b = df["revenue"] / 1e9 if "revenue" in df else None
    gm = (df.get("gross_profit") / df["revenue"]) * 100 if "gross_profit" in df else None
    om = (df.get("operating_income") / df["revenue"]) * 100 if "operating_income" in df else None
    fcfm = (df.get("fcf") / df["revenue"]) * 100 if "fcf" in df else None

    fig = go.Figure()
    if rev_b is not None:
        fig.add_bar(x=df.index, y=rev_b, name="Выручка ($B)",
                    marker_color=GREY, opacity=0.35, yaxis="y2",
                    hovertemplate="%{y:.1f}B<extra>Выручка</extra>")
    if gm is not None:
        fig.add_scatter(x=df.index, y=gm, name="Валовая маржа %", mode="lines+markers",
                        line=dict(color=GREEN, width=2),
                        hovertemplate="%{y:.1f}%<extra>GM</extra>")
    if om is not None:
        fig.add_scatter(x=df.index, y=om, name="Операц. маржа %", mode="lines+markers",
                        line=dict(color=BLUE, width=2),
                        hovertemplate="%{y:.1f}%<extra>OM</extra>")
    if fcfm is not None:
        fig.add_scatter(x=df.index, y=fcfm, name="FCF маржа %", mode="lines+markers",
                        line=dict(color="#9333ea", width=2),
                        hovertemplate="%{y:.1f}%<extra>FCF</extra>")
    fig.update_layout(
        title="10 лет: выручка и маржинальность",
        yaxis=dict(title="% от выручки", ticksuffix="%"),
        yaxis2=dict(title="Выручка $B", overlaying="y", side="right", showgrid=False),
        margin=dict(t=60, l=40, r=40, b=40), height=400,
        plot_bgcolor="white", hovermode="x unified",
        legend=dict(orientation="h", y=-0.15),
    )
    return fig


def segment_pie(segments: dict[str, float]) -> Optional[go.Figure]:
    """segments = {segment_name: revenue_value}."""
    if not segments:
        return None
    labels = list(segments.keys())
    values = [float(v) if v is not None else 0.0 for v in segments.values()]
    if not labels:
        return None

    total = sum(values)
    if total <= 0:
        # Доли не раскрыты — рисуем равные дольки как визуальный список
        values = [1.0] * len(labels)
        custom_labels = labels
        title = "Направления выручки (доли не раскрыты в 10-K)"
        hover = "%{label}<extra></extra>"
    else:
        custom_labels = [f"{l} — {v/total*100:.0f}%" for l, v in zip(labels, values)]
        title = "Откуда выручка"
        hover = "%{label}: %{percent}<extra></extra>"

    # Длинные подписи → внутри как %, имена выносим в легенду справа
    # Снижаем порог до 18 chars, потому что outside-подписи у круга жмутся текстом и обрезаются.
    has_long = any(len(l) > 18 for l in labels) or len(labels) > 4
    if has_long:
        fig = go.Figure(go.Pie(
            labels=labels, values=values, hole=0.45,
            textinfo="percent", textposition="inside",
            marker=dict(line=dict(color="white", width=2)),
            hovertemplate=hover,
            sort=False,
        ))
        fig.update_layout(
            title=title,
            margin=dict(t=60, l=20, r=20, b=20), height=400,
            showlegend=True,
            legend=dict(orientation="v", x=1.0, y=0.5, font=dict(size=11)),
        )
    else:
        fig = go.Figure(go.Pie(
            labels=labels, values=values, hole=0.45,
            text=custom_labels, textinfo="text", textposition="outside",
            marker=dict(line=dict(color="white", width=2)),
            hovertemplate=hover,
            sort=False,
        ))
        fig.update_layout(
            title=title,
            margin=dict(t=60, l=60, r=60, b=20), height=380,
            showlegend=False, uniformtext=dict(minsize=10, mode="show"),
        )
    return fig


def moat_bars(comparisons: list[dict]) -> go.Figure:
    """Horizontal bars: company vs sector vs market for each metric.

    comparisons = [
        {"metric": "GM %", "company": 47.0, "sector": 42.0, "market": 35.0, "color": "green"},
        ...
    ]
    """
    colors = {"green": GREEN, "yellow": YELLOW, "red": RED}
    fig = go.Figure()
    metrics = [c["metric"] for c in comparisons]

    fig.add_bar(
        y=metrics, x=[c["company"] for c in comparisons], name="Компания",
        orientation="h",
        marker_color=[colors.get(c.get("color", "yellow"), YELLOW) for c in comparisons],
        text=[f"{c['company']:.1f}" for c in comparisons], textposition="outside",
    )
    # Маркеры рисуем только там, где медиана не None
    sec_y = [m for m, c in zip(metrics, comparisons) if c.get("sector") is not None]
    sec_x = [c["sector"] for c in comparisons if c.get("sector") is not None]
    if sec_x:
        fig.add_scatter(
            y=sec_y, x=sec_x, name="Медиана сектора",
            mode="markers", marker=dict(symbol="line-ns-open", size=22, color="#111", line=dict(width=3)),
            hovertemplate="Сектор: %{x:.1f}<extra></extra>",
        )
    mkt_y = [m for m, c in zip(metrics, comparisons) if c.get("market") is not None]
    mkt_x = [c["market"] for c in comparisons if c.get("market") is not None]
    if mkt_x:
        fig.add_scatter(
            y=mkt_y, x=mkt_x, name="Медиана SP500",
            mode="markers", marker=dict(symbol="line-ns-open", size=22, color=GREY, line=dict(width=3)),
            hovertemplate="SP500: %{x:.1f}<extra></extra>",
        )
    fig.update_layout(
        title="Метрики компании vs Сектор vs S&P 500",
        barmode="overlay", margin=dict(t=60, l=10, r=40, b=40), height=380,
        plot_bgcolor="white", legend=dict(orientation="h", y=-0.15),
        xaxis=dict(showgrid=True, gridcolor="#eee"),
    )
    return fig
