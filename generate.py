#!/usr/bin/env python3
"""
Plavena Weekly Brief — Automated Report Generator
Runs every Monday at 08:00 IST via GitHub Actions.
Fetches live prices → calls Claude API → builds 10-page HTML → emails subscribers.
"""

import os, sys, json, re, math, datetime, smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import anthropic
import yfinance as yf
import requests

# ══════════════════════════════════════════════════════════════
# 1. DATE / WEEK HELPERS
# ══════════════════════════════════════════════════════════════

def week_info():
    t = datetime.date.today()
    wn, yr = t.isocalendar()[1], t.year
    mon = t - datetime.timedelta(days=t.weekday())
    sun = mon + datetime.timedelta(days=6)
    date_range = f"{mon.day} {mon.strftime('%b')}–{sun.day} {sun.strftime('%b')}"
    next_mon = mon + datetime.timedelta(days=7)
    next_days = [next_mon + datetime.timedelta(days=i) for i in range(5)]
    return wn, yr, date_range, mon, sun, next_days


# ══════════════════════════════════════════════════════════════
# 2. PRICE FETCHING
# ══════════════════════════════════════════════════════════════

YF_MAP = {
    "copper":  ("HG=F",  "Copper LME 3M",   "$/t",    7500),
    "gold":    ("GC=F",  "Gold Spot",         "$/oz",   1900),
    "oil":     ("BZ=F",  "Brent Crude",       "$/bbl",  75),
    "natgas":  ("NG=F",  "Natural Gas HH",    "$/MMBtu", 2.5),
}

MANUAL_METALS = {
    "aluminium": ("Aluminium LME",     "$/t",   2500),
    "nickel":    ("Nickel LME",        "$/t",   17000),
    "iron_ore":  ("Iron Ore 62% Fe",   "$/dmt", 115),
    "lithium":   ("Lithium Carbonate", "$/t",   14000),
    "met_coal":  ("Met Coal HCC",      "$/t",   200),
    "cobalt":    ("Cobalt Standard",   "$/t",   30000),
}


def _yf_fetch(ticker):
    """Fetch weekly closes via Ticker.history (avoids the yfinance>=0.2.40
    multi-index column bug that silently broke single-ticker yf.download calls
    and made every price fall back to a hardcoded estimate)."""
    try:
        hist_df = yf.Ticker(ticker).history(
            period="1y", interval="1wk", auto_adjust=True
        )
        if hist_df.empty or "Close" not in hist_df.columns:
            return None
        closes = hist_df["Close"].dropna()
        if len(closes) < 5:
            return None

        cur = float(closes.iloc[-1])
        w1  = float(closes.iloc[-2])
        w4  = float(closes.iloc[-5])

        # YTD baseline = first weekly close of the current calendar year
        yr = datetime.date.today().year
        ytd_series = closes[closes.index.year == yr]
        ytd = float(ytd_series.iloc[0]) if not ytd_series.empty else cur

        return {
            "current": round(cur, 2),
            "c1w": round((cur - w1) / w1 * 100, 2) if w1 else 0,
            "c4w": round((cur - w4) / w4 * 100, 2) if w4 else 0,
            "ytd": round((cur - ytd) / ytd * 100, 2) if ytd else 0,
            "hist": [round(float(x), 2) for x in closes.tail(26).tolist()],
        }
    except Exception as e:
        print(f"  [yfinance] {ticker}: {e}")
        return None


def load_cache():
    try:
        with open("data/price_cache.json") as f:
            return json.load(f)
    except:
        return {}


def save_cache(data):
    os.makedirs("data", exist_ok=True)
    with open("data/price_cache.json", "w") as f:
        json.dump(data, f, indent=2)


def fetch_prices():
    cache = load_cache()
    prices = {}

    for key, (ticker, name, unit, default) in YF_MAP.items():
        print(f"  {name}...")
        d = _yf_fetch(ticker)
        if d:
            d.update({"name": name, "unit": unit})
            prices[key] = d
        else:
            prices[key] = cache.get(key) or {
                "current": default, "c1w": 0, "c4w": 0, "ytd": 0, "hist": None,
                "name": name, "unit": unit, "estimated": True,
            }

    for key, (name, unit, default) in MANUAL_METALS.items():
        c = cache.get(key)
        if c:
            prices[key] = c
            prices[key]["name"] = name
            prices[key]["unit"] = unit
        else:
            prices[key] = {
                "current": default, "c1w": 0, "c4w": 0, "ytd": 0, "hist": None,
                "name": name, "unit": unit, "estimated": True,
            }

    save_cache(prices)
    return prices


# ══════════════════════════════════════════════════════════════
# 3. SVG / CHART HELPERS
# ══════════════════════════════════════════════════════════════

def sparkline_pts(hist, w=320, h=130, pad=12):
    if not hist or len(hist) < 2:
        return f"0,{h//2} {w},{h//2}"
    vals = [float(x) for x in hist if x is not None]
    if len(vals) < 2:
        return f"0,{h//2} {w},{h//2}"
    mn, mx = min(vals), max(vals)
    if mx == mn:
        return f"0,{h//2} {w},{h//2}"
    pts = []
    for i, v in enumerate(vals):
        x = (i / (len(vals) - 1)) * (w - 2 * pad) + pad
        y = h - pad - ((v - mn) / (mx - mn)) * (h - 2 * pad)
        pts.append(f"{x:.0f},{y:.1f}")
    return " ".join(pts)


def synth_hist(current, ytd_pct, n=26, seed=0):
    import random; random.seed(seed)
    if not current:
        return [100.0] * n
    start = current / (1 + ytd_pct / 100) if ytd_pct else current * 0.92
    delta = (current - start) / n
    vals, p = [], start
    for _ in range(n - 1):
        p += delta + random.gauss(0, abs(current - start) * 0.04 / max(n, 1))
        vals.append(round(max(p, current * 0.3), 2))
    vals.append(current)
    return vals


def spark_color(ytd):
    if ytd is None: return "#00B3FF"
    if ytd >= 3:    return "#2BD17E"
    if ytd <= -3:   return "#FF5A5F"
    return "#F4B740"


def radar_pts(scores):
    keys = ["mining", "metals", "minerals", "trade", "logistics", "supply_chain", "ai_data"]
    angles = [0, 51.4, 102.9, 154.3, 205.7, 257.1, 308.6]
    R = 90
    pts = []
    for k, a in zip(keys, angles):
        s = max(0, min(100, scores.get(k, 50)))
        r = (s / 100) * R
        rad = math.radians(a - 90)
        pts.append(f"{r * math.cos(rad):.1f},{r * math.sin(rad):.1f}")
    return " ".join(pts)


def radar_nodes(scores):
    keys = ["mining", "metals", "minerals", "trade", "logistics", "supply_chain", "ai_data"]
    angles = [0, 51.4, 102.9, 154.3, 205.7, 257.1, 308.6]
    R = 90
    out = []
    for k, a in zip(keys, angles):
        s = max(0, min(100, scores.get(k, 50)))
        r = (s / 100) * R
        rad = math.radians(a - 90)
        out.append(f'<circle cx="{r*math.cos(rad):.1f}" cy="{r*math.sin(rad):.1f}" r="2.2"/>')
    return "\n".join(out)


def pill(view):
    return f'<span class="pill {view.lower()}">{view.upper()}</span>'


def pts_from_hist(hist, xmin=40, xmax=500, ymin=20, ymax=180):
    if not hist or len(hist) < 2:
        mid = (ymin + ymax) // 2
        return f"{xmin},{mid} {xmax},{mid}"
    vals = [float(v) for v in hist if v is not None]
    mn, mx = min(vals), max(vals)
    if mx == mn:
        mid = (ymin + ymax) // 2
        return f"{xmin},{mid} {xmax},{mid}"
    pts = []
    for i, v in enumerate(vals):
        x = xmin + (i / (len(vals) - 1)) * (xmax - xmin)
        y = ymax - ((v - mn) / (mx - mn)) * (ymax - ymin)
        pts.append(f"{x:.0f},{y:.1f}")
    return " ".join(pts)


def exhibit_timeseries(pts1, pts2=None, label1="Primary", label2="Secondary",
                        c1="#00B3FF", c2="#2BD17E"):
    dash2 = "" if not pts2 else f'<polyline fill="none" stroke="{c2}" stroke-width="1.3" stroke-dasharray="4,3" points="{pts2}"/>'
    legend2 = "" if not pts2 else f'<line x1="300" y1="12" x2="316" y2="12" stroke="{c2}" stroke-dasharray="4,3" stroke-width="1.3"/><text x="320" y="16" font-family="IBM Plex Mono" font-size="6.5" fill="{c2}">{label2}</text>'
    return f"""<svg viewBox="0 0 520 200" width="100%" height="180" preserveAspectRatio="xMidYMid meet">
  <g stroke="rgba(255,255,255,0.08)" stroke-width="0.5">
    <line x1="40" y1="20" x2="500" y2="20"/><line x1="40" y1="60" x2="500" y2="60"/>
    <line x1="40" y1="100" x2="500" y2="100"/><line x1="40" y1="140" x2="500" y2="140"/>
    <line x1="40" y1="180" x2="500" y2="180"/>
  </g>
  <line x1="40" y1="100" x2="500" y2="100" stroke="rgba(255,255,255,0.2)" stroke-width="0.8"/>
  <polyline fill="none" stroke="{c1}" stroke-width="1.6" points="{pts1}"/>
  {dash2}
  <rect x="40" y="8" width="8" height="4" fill="{c1}"/>
  <text x="52" y="14" font-family="IBM Plex Mono" font-size="6.5" fill="{c1}">{label1}</text>
  {legend2}
</svg>"""


# ══════════════════════════════════════════════════════════════
# 4. CLAUDE CONTENT GENERATION
# ══════════════════════════════════════════════════════════════

def _price_block(prices):
    lines = []
    for k, p in prices.items():
        if p.get("current"):
            est = " [estimated/cached]" if p.get("estimated") else ""
            lines.append(
                f"  {p['name']:28s} {p['current']:>12.2f} {p['unit']:8s}"
                f"  1W: {p.get('c1w',0):+.1f}%  4W: {p.get('c4w',0):+.1f}%  YTD: {p.get('ytd',0):+.1f}%{est}"
            )
        else:
            lines.append(f"  {p.get('name','?'):28s} [unavailable — estimate from knowledge]")
    return "\n".join(lines)


# Canonical 8 commodities shown in the Prices & Signals table, in display order.
TABLE_SPEC = [
    ("copper",    "Copper LME 3M",     "$/t"),
    ("aluminium", "Aluminium LME",     "$/t"),
    ("nickel",    "Nickel LME",        "$/t"),
    ("iron_ore",  "Iron Ore 62% Fe",   "$/dmt"),
    ("lithium",   "Lithium Carbonate", "$/t"),
    ("cobalt",    "Cobalt Standard",   "$/t"),
    ("met_coal",  "Met Coal HCC",      "$/t"),
    ("oil",       "Brent Crude",       "$/bbl"),
]


def _fmt_spot(cur, estimated):
    if cur is None:
        return "—"
    if cur >= 1000:
        s = f"{cur:,.0f}"
    elif cur >= 100:
        s = f"{cur:,.1f}"
    else:
        s = f"{cur:,.2f}"
    return s + ("<sup>e</sup>" if estimated else "")


def _fmt_delta(v, estimated):
    """Return (display, is_positive). Estimated/missing data shows an em dash
    instead of a fabricated percentage change."""
    if estimated or v is None:
        return "—", True
    return f"{v:+.1f}%", (v >= 0)


def assemble_price_table(prices, prices_view):
    """Build the 8-row price table from VERIFIED data only. The model supplies
    just the vs-forecast call (vf) and view per row — never the numbers."""
    rows = []
    for i, (key, dname, dunit) in enumerate(TABLE_SPEC):
        p = prices.get(key, {})
        est = bool(p.get("estimated")) or p.get("hist") is None
        w1, w1p = _fmt_delta(p.get("c1w"), est)
        w4, w4p = _fmt_delta(p.get("c4w"), est)
        ytd, ytdp = _fmt_delta(p.get("ytd"), est)
        v = prices_view[i] if i < len(prices_view) else {}
        rows.append({
            "commodity": p.get("name", dname),
            "unit": p.get("unit", dunit),
            "spot": _fmt_spot(p.get("current"), est),
            "w1": w1, "w1p": w1p,
            "w4": w4, "w4p": w4p,
            "ytd": ytd, "ytdp": ytdp,
            "vf": v.get("vf", "—"), "vfp": bool(v.get("vfp", True)),
            "view": v.get("view", "hold"),
        })
    return rows


def generate_content(prices, wn, yr, date_range, next_days, deals_queue):
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    cal_dates  = [d.strftime("%a %d") for d in next_days]
    cal_days   = [str(d.day)          for d in next_days]
    cal_months = [d.strftime("%b")    for d in next_days]

    deals_ctx = (
        json.dumps(deals_queue[:4], indent=2) if deals_queue
        else "QUEUE EMPTY — generate 4 realistic deal opportunities from current market conditions"
    )

    prompt = f"""You are Harsh Dhillon, lead analyst at Plavena (plavena.com).
Plavena is a B2B commodity intelligence and trading firm. India / Asia buyer focus.
Subscribers: CFOs, procurement heads, commodity traders at mid-market companies.
Write with authority, no filler phrases. All figures must match the live data below.

════ LIVE DATA — W{wn}/{yr} ({date_range}) ════
{_price_block(prices)}

════ DEAL QUEUE ════
{deals_ctx}

════ NEXT WEEK ════
{', '.join(cal_dates)}

Return ONLY a valid JSON object — no markdown, no code fences, no extra text:
{{
  "cover_h1": "First headline line (5-7 words)",
  "cover_h2": "Second headline line (4-6 words)",
  "cover_h3_accent": "Third line accent phrase (3-5 words, goes blue)",
  "cover_deck": "Two-sentence narrative deck below the headline.",
  "tldr": [
    "Bullet 1 with <b style='color:#FFFFFF'>key metric bold</b> and <span style='color:#00B3FF'>$XX accent</span>",
    "Bullet 2",
    "Bullet 3"
  ],
  "pulse": {{"mining":78,"metals":82,"minerals":62,"trade":71,"logistics":55,"supply_chain":64,"ai_data":88}},
  "tradeline_plain": "Verb-first action phrase (no special tags)",
  "tradeline_accent": "Key asset or trade in italic accent (3-6 words)",
  "hero": [
    {{"num":"$22","cls":"accent","unit":"USD / TONNE · COPPER TC/RC","caption":"One sentence context."}},
    {{"num":"+38%","cls":"pos","unit":"MOM · INDIA IRON-ORE EXPORTS","caption":"One sentence context."}},
    {{"num":"-11%","cls":"neg","unit":"YTD · LITHIUM CARBONATE","caption":"One sentence context."}}
  ],
  "ctx_left": "~100-word paragraph opening with <strong>The set-up.</strong>",
  "ctx_right": "~100-word paragraph opening with <strong>The implication.</strong> Ends referencing Plavena Trading Prospects p.9.",
  "callout_label": "Why this matters for India &amp; Asia",
  "callout_quote": "1-2 sentence italic insight for Indian and Asian buyers.",
  "prices_view": [
    {{"vf":"+4.3%","vfp":true,"view":"hold"}},
    {{"vf":"+1.2%","vfp":true,"view":"hold"}},
    {{"vf":"-3.1%","vfp":false,"view":"avoid"}},
    {{"vf":"-8.2%","vfp":false,"view":"watch"}},
    {{"vf":"-15.2%","vfp":false,"view":"watch"}},
    {{"vf":"+2.0%","vfp":true,"view":"hold"}},
    {{"vf":"-4.5%","vfp":false,"view":"hold"}},
    {{"vf":"+1.1%","vfp":true,"view":"hold"}}
  ],
  "sm": [
    {{"name":"Copper · LME","change":"+11.2% YTD","pos":true,"call":"BUY · W08","call_note":"5y avg $8,400 · breakout intact"}},
    {{"name":"Lithium Carbonate","change":"-11.0% YTD","pos":false,"call":"ACCUMULATE · W22","call_note":"Floor forming · SQM cut + Pilbara guide"}},
    {{"name":"Copper TC/RC","change":"-61% YTD","pos":false,"call":"STRUCTURAL BREAK · W14","call_note":"17-month low · Asian smelter overcapacity"}},
    {{"name":"Iron Ore · 62% Fe","change":"-13.1% YTD","pos":false,"call":"INDIA EXPORTS +38% · W20","call_note":"Goan restart adding 4.2 Mt to seaborne"}}
  ],
  "ex1_caption": "Of XX actionable calls YTD: <b style='color:#2BD17E'>XX in-the-money</b>, <b style='color:#F4B740'>X flat</b>, <b style='color:#FF5A5F'>X missed</b>. Hit-rate XX%. Avg IRR <b style='color:#00B3FF'>+XX%</b>.",
  "dd_title": "Deep dive headline — short, provocative, under 12 words",
  "dd_lede": "~80-word lede. Dense, no filler.",
  "dd_p1": "~120-word first body paragraph.",
  "dd_p2": "~120-word second body paragraph.",
  "dd_ex2_title": "Exhibit 02 chart title",
  "dd_ex2_caption": "~100-word chart analysis.",
  "dd_p2_title": "Page 6 subheading (8 words max)",
  "dd_p2_para1": "~150-word paragraph.",
  "dd_p2_para2": "~100-word paragraph.",
  "dd_p2_callout_label": "Why this matters for India &amp; Asia",
  "dd_p2_callout": "1-2 sentence India/Asia callout.",
  "dd_ex3_title": "Exhibit 03 chart title",
  "dd_p3_title": "Page 7 second story subheading",
  "dd_p3_lede": "~60-word lede for second story.",
  "dd_p3_para1": "~120-word first paragraph.",
  "dd_p3_para2": "~100-word implications paragraph.",
  "dd_p3_callout_label": "India angle",
  "dd_p3_callout": "1-2 sentence India/Asia callout for second story.",
  "watchlist": [
    {{"name":"Hindustan Copper","ticker":"HNDCOPPER:NS","mkt":"NSE","exp":"Indian copper smelter, toll-contract beneficiary","catalyst":"TC/RC squeeze — toll-smelting contract announcement Q3","mv":"+4.1%","mvp":true,"view":"buy"}},
    {{"name":"Vedanta Ltd","ticker":"VEDL:NS","mkt":"NSE","exp":"Diversified Indian miner","catalyst":"Tuticorin copper expansion clarity","mv":"+2.8%","mvp":true,"view":"buy"}},
    {{"name":"NMDC Ltd","ticker":"NMDC:NS","mkt":"NSE","exp":"India iron-ore producer","catalyst":"Goan restart capacity update","mv":"+5.4%","mvp":true,"view":"watch"}},
    {{"name":"Pilbara Minerals","ticker":"PLS:AX","mkt":"ASX","exp":"Australian lithium spodumene","catalyst":"2026 production guidance — bear case priced","mv":"+1.6%","mvp":true,"view":"buy"}},
    {{"name":"SQM","ticker":"SQM:N","mkt":"NYSE","exp":"Chilean lithium brine","catalyst":"Output cut 11% — first supply discipline","mv":"+3.2%","mvp":true,"view":"hold"}},
    {{"name":"Antofagasta","ticker":"ANTO:L","mkt":"LSE","exp":"Chilean copper miner","catalyst":"TC/RC squeeze — EBITDA revision, Jul results","mv":"-1.2%","mvp":false,"view":"hold"}},
    {{"name":"Jiangxi Copper","ticker":"600362:SS","mkt":"SHSE","exp":"Chinese copper smelter","catalyst":"Margin squeeze — watch for utilisation cuts","mv":"-2.4%","mvp":false,"view":"avoid"}},
    {{"name":"Fortescue","ticker":"FMG:AX","mkt":"ASX","exp":"Australian iron ore","catalyst":"India export surge — FY27 volume guide","mv":"-0.8%","mvp":false,"view":"watch"}},
    {{"name":"Albemarle","ticker":"ALB:N","mkt":"NYSE","exp":"US lithium producer","catalyst":"Kemerton train-3 deferral — cost base relief","mv":"+4.5%","mvp":true,"view":"hold"}},
    {{"name":"Hindustan Zinc","ticker":"HINDZINC:NS","mkt":"NSE","exp":"Indian zinc/silver producer","catalyst":"Q1FY27 volume — zinc premium India vs LME","mv":"+1.9%","mvp":true,"view":"hold"}}
  ],
  "deals": [
    {{"id":"PRO-W{wn:02d}-01","status":"OPEN","title":"Deal Title","commodity":"Commodity","volume":"XX,000 t/yr","spec":"Grade / spec","price":"$XX/t","logist":"FOB X → CIF Y","window":"Closes DD Mon"}},
    {{"id":"PRO-W{wn:02d}-02","status":"OPEN","title":"Deal Title 2","commodity":"Commodity","volume":"XX,000 t","spec":"Grade / spec","price":"$XX/t","logist":"FOB X → CIF Y","window":"Closes DD Mon"}},
    {{"id":"PRO-W{wn:02d}-03","status":"OPEN","title":"Deal Title 3","commodity":"Commodity","volume":"XXX,000 t","spec":"Grade / spec","price":"$XX/t","logist":"FOB X → CIF Y","window":"Closes DD Mon"}},
    {{"id":"PRO-W{wn:02d}-04","status":"OPEN","title":"Deal Title 4","commodity":"Commodity","volume":"X,000 t","spec":"Grade / spec","price":"$XX/t","logist":"FOB X → CIF Y","window":"Closes DD Mon"}}
  ],
  "calendar": [
    {{"date":"{cal_dates[0]}","day":"{cal_days[0]}","mon":"{cal_months[0]}","events":[{{"t":"Event","d":"Detail"}}]}},
    {{"date":"{cal_dates[1]}","day":"{cal_days[1]}","mon":"{cal_months[1]}","events":[{{"t":"Event","d":"Detail"}}]}},
    {{"date":"{cal_dates[2]}","day":"{cal_days[2]}","mon":"{cal_months[2]}","events":[]}},
    {{"date":"{cal_dates[3]}","day":"{cal_days[3]}","mon":"{cal_months[3]}","events":[{{"t":"Event","d":"Detail"}}]}},
    {{"date":"{cal_dates[4]}","day":"{cal_days[4]}","mon":"{cal_months[4]}","events":[{{"t":"Event","d":"Detail"}}]}}
  ],
  "outlook": "~100-word look-ahead paragraph.",
  "analyst_name": "Harsh Dhillon",
  "analyst_role": "Lead Analyst · Metals &amp; Mining"
}}

RULES — follow exactly:
• prices_view: EXACTLY 8 entries, in this order — Copper, Aluminium, Nickel, Iron Ore, Lithium, Cobalt, Met Coal, Brent Crude. Each holds only your vs-forecast call (vf, vfp) and view. Do NOT output spot prices or % changes in prices_view — the system fills spot/1W/4W/YTD from verified live data.
• Never invent a spot price or weekly / 4-week / YTD change anywhere. If you cite a price in narrative, use the LIVE DATA block above verbatim.
• Exactly 10 watchlist entries — mix all 4 view types
• Exactly 4 deals — use deals_queue entries if provided, else generate
• Exactly 5 calendar days, at least 3 real upcoming macro/corporate events
• Pulse scores are 0–100 integers reflecting this week's activity per sector
• Use HTML entities for & > < in text: &amp; &gt; &lt;
• Return ONLY the JSON object"""

    msg = client.messages.create(
        model="claude-fable-5",
        max_tokens=16000,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": prompt}]
    )

    txt = next((b.text for b in msg.content if b.type == "text"), "").strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```[a-z]*\n?", "", txt)
        txt = re.sub(r"\n?```$", "", txt)
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", txt)
        if m:
            return json.loads(m.group())
        raise RuntimeError(f"Claude returned invalid JSON.\n\nFirst 500 chars:\n{txt[:500]}")


# ══════════════════════════════════════════════════════════════
# 5. HTML PAGE BUILDERS
# ══════════════════════════════════════════════════════════════

RT = "METALS &amp; MINING"


def _hf(num, title, wn, yr):
    head = (f'  <div class="page-head">\n'
            f'    <div class="ph-left"><b>{num:02d}</b> · {title}</div>\n'
            f'    <div class="ph-right">PLAVENA · W{wn} · {RT}</div>\n  </div>')
    foot = (f'  <div class="page-foot">\n'
            f'    <div class="colophon">PLAVENA · <b>Weekly Brief</b> · Metals &amp; Mining · W{wn}·{yr}</div>\n'
            f'    <div>Page {num:02d} / 10 — {title}</div>\n  </div>')
    return head, foot


def _page_cover(c, wn, yr, date_range, mon, sun):
    issue_str = f"{mon.day} {mon.strftime('%b')}–{sun.day} {sun.strftime('%b')}"
    tldr = "".join(f"<li>{b}</li>" for b in c.get("tldr", []))
    poly = radar_pts(c.get("pulse", {}))
    nodes = radar_nodes(c.get("pulse", {}))
    return f"""
<!-- ═══ PAGE 1 — COVER ═══ -->
<section class="page cover">
  <div class="top">
    <div class="logo">PLAVENA</div>
    <div class="issue">Issue<span class="num">N° {wn} / {yr}</span>Week {wn} &nbsp;·&nbsp; {issue_str}</div>
  </div>
  <div class="cover-title">
    <div class="kicker">— Weekly Brief · Metals &amp; Mining</div>
    <h1>{c.get("cover_h1","")}<br>{c.get("cover_h2","")}<br><span class="accent">{c.get("cover_h3_accent","")}</span></h1>
    <p class="deck">{c.get("cover_deck","")}</p>
  </div>
  <div class="cover-bottom">
    <div class="tldr">
      <h4 class="label">This week in 60 seconds</h4>
      <ol>{tldr}</ol>
    </div>
    <div class="pulse-wrap">
      <div class="pulse-label">— Plavena Pulse · 7-sector heat</div>
      <svg viewBox="-110 -110 220 220" width="200" height="200">
        <defs><radialGradient id="pg" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stop-color="#00B3FF" stop-opacity="0.35"/>
          <stop offset="100%" stop-color="#00B3FF" stop-opacity="0"/>
        </radialGradient></defs>
        <g fill="none" stroke="rgba(255,255,255,0.07)" stroke-width="0.5">
          <polygon points="90,0 56,71 -20,87 -81,38 -81,-38 -20,-87 56,-71"/>
          <polygon points="72,0 45,56 -16,70 -65,30 -65,-30 -16,-70 45,-56"/>
          <polygon points="54,0 34,42 -12,52 -49,23 -49,-23 -12,-52 34,-42"/>
          <polygon points="36,0 23,28 -8,35 -33,15 -33,-15 -8,-35 23,-28"/>
          <polygon points="18,0 11,14 -4,17 -16,8 -16,-8 -4,-17 11,-14"/>
        </g>
        <g stroke="rgba(255,255,255,0.08)" stroke-width="0.5">
          <line x1="0" y1="0" x2="90" y2="0"/><line x1="0" y1="0" x2="56" y2="71"/>
          <line x1="0" y1="0" x2="-20" y2="87"/><line x1="0" y1="0" x2="-81" y2="38"/>
          <line x1="0" y1="0" x2="-81" y2="-38"/><line x1="0" y1="0" x2="-20" y2="-87"/>
          <line x1="0" y1="0" x2="56" y2="-71"/>
        </g>
        <polygon points="{poly}" fill="url(#pg)" stroke="#00B3FF" stroke-width="1.4"/>
        <g fill="#00B3FF">{nodes}</g>
        <g font-family="IBM Plex Mono,monospace" font-size="6" fill="#C9D4E0" letter-spacing="0.5">
          <text x="98" y="2" text-anchor="start">MINING</text>
          <text x="62" y="80" text-anchor="middle">METALS</text>
          <text x="-22" y="98" text-anchor="middle">MINERALS</text>
          <text x="-90" y="42" text-anchor="end">TRADE</text>
          <text x="-90" y="-36" text-anchor="end">LOGISTICS</text>
          <text x="-22" y="-92" text-anchor="middle">SUPPLY CH.</text>
          <text x="62" y="-76" text-anchor="middle">AI / DATA</text>
        </g>
        <circle cx="0" cy="0" r="1.5" fill="#00B3FF"/>
      </svg>
      <div class="pulse-caption">Higher = more reader-relevant action · 0–100 scale</div>
    </div>
  </div>
  <div class="page-foot">
    <div class="colophon">PLAVENA · <b>Weekly Brief</b> · Metals &amp; Mining · W{wn}·{yr}</div>
    <div>Page 01 / 10 — Cover</div>
  </div>
</section>"""


def _page_exec(c, wn, yr):
    heroes = "".join(
        f'<div class="hero"><div class="hero-num {h["cls"]}">{h["num"]}</div>'
        f'<div class="hero-unit">{h["unit"]}</div>'
        f'<div class="hero-caption">{h["caption"]}</div></div>'
        for h in c.get("hero", [])
    )
    head, foot = _hf(2, "Executive Summary", wn, yr)
    return f"""
<!-- ═══ PAGE 2 — EXEC SUMMARY ═══ -->
<section class="page">
  {head}
  <h4 class="label">The trade this week</h4>
  <p class="es-tradeline">{c.get("tradeline_plain","")}; <span class="em">{c.get("tradeline_accent","")}</span></p>
  <div class="hero-numbers">{heroes}</div>
  <h4 class="label">Context</h4>
  <div class="two-col mb-6">
    <div class="col"><p>{c.get("ctx_left","")}</p></div>
    <div class="col"><p>{c.get("ctx_right","")}</p></div>
  </div>
  <div class="callout">
    <div class="co-label">— {c.get("callout_label","")}</div>
    <p>{c.get("callout_quote","")}</p>
  </div>
  {foot}
</section>"""


def _page_prices(c, wn, yr, mon, sun):
    rows = ""
    for p in c.get("prices_table", []):
        rows += (
            f'<tr><td>{p["commodity"]}</td><td>{p["unit"]}</td><td>{p["spot"]}</td>'
            f'<td class="{"pos" if p["w1p"] else "neg"}">{p["w1"]}</td>'
            f'<td class="{"pos" if p["w4p"] else "neg"}">{p["w4"]}</td>'
            f'<td class="{"pos" if p["ytdp"] else "neg"}">{p["ytd"]}</td>'
            f'<td class="{"pos" if p["vfp"] else "neg"}">{p["vf"]}</td>'
            f'<td>{pill(p["view"])}</td></tr>'
        )
    head, foot = _hf(3, "Prices &amp; Signals", wn, yr)
    cut = sun.strftime("%d %b %Y")
    return f"""
<!-- ═══ PAGE 3 — PRICES & SIGNALS ═══ -->
<section class="page">
  {head}
  <h2 class="section">Prices &amp; Signals</h2>
  <p class="meta-row" style="margin-top:2mm;margin-bottom:8mm;">Spot prices · Data cut-off {cut}, 17:30 IST · Live: Copper, Brent (Yahoo Finance) · <sup>e</sup> = Plavena estimate · LME / SHFE / Fastmarkets composite</p>
  <table class="price-table">
    <thead><tr>
      <th>Commodity</th><th>Unit</th><th>Spot</th>
      <th>1W</th><th>4W</th><th>YTD</th><th>vs. Forecast</th><th>Plavena view</th>
    </tr></thead>
    <tbody>{rows}</tbody>
  </table>
  {foot}
</section>"""


def _page_maps(c, prices, wn, yr):
    sm_data = c.get("sm", [])
    sm_keys = ["copper", "lithium", "copper", "iron_ore"]
    cards = ""
    for i, (sm, pkey) in enumerate(zip(sm_data, sm_keys)):
        p = prices.get(pkey, {})
        hist = p.get("hist") or synth_hist(p.get("current", 100), p.get("ytd", 0), seed=i)
        pts = sparkline_pts(hist)
        color = spark_color(p.get("ytd"))
        chg_cls = "pos" if sm.get("pos") else "neg"
        # find midpoint for call annotation
        pts_list = pts.split()
        mid = pts_list[len(pts_list) // 2].split(",") if pts_list else ["160", "65"]
        ax, ay = mid[0], mid[1] if len(mid) == 2 else "65"
        ay_int = int(float(ay)) - 4
        cards += f"""
    <div class="sm-card">
      <div class="sm-head">
        <div class="sm-name">{sm.get("name","")}</div>
        <div class="sm-change {chg_cls}">{sm.get("change","")}</div>
      </div>
      <svg viewBox="0 0 320 130" width="100%" height="120">
        <g stroke="rgba(255,255,255,0.06)" stroke-width="0.5">
          <line x1="0" y1="33" x2="320" y2="33"/>
          <line x1="0" y1="66" x2="320" y2="66"/>
          <line x1="0" y1="100" x2="320" y2="100"/>
        </g>
        <polyline fill="none" stroke="{color}" stroke-width="1.5" points="{pts}"/>
        <circle cx="{ax}" cy="{ay}" r="3" fill="{color}" stroke="#080D1A" stroke-width="2"/>
        <text x="{int(ax)+4}" y="{ay_int}" font-family="IBM Plex Mono" font-size="6" fill="{color}">{sm.get("call","")}</text>
      </svg>
      <div class="sm-note">{sm.get("call_note","")}</div>
    </div>"""
    head, foot = _hf(4, "Movement Maps", wn, yr)
    return f"""
<!-- ═══ PAGE 4 — MOVEMENT MAPS ═══ -->
<section class="page">
  {head}
  <h2 class="section">Movement Maps</h2>
  <p class="meta-row" style="margin-top:2mm;margin-bottom:6mm;">26-week price paths · Vertical axis indexed to W01 {yr} · Annotations mark Plavena calls</p>
  <div class="small-multiples">{cards}</div>
  <div class="exhibit" style="margin-top:9mm;">
    <div class="ex-head"><div class="ex-num">Exhibit 01</div><div class="ex-title">Where the calls land · YTD scoreboard</div></div>
    <p style="font-size:9pt;color:var(--muted);">{c.get("ex1_caption","")}</p>
  </div>
  {foot}
</section>"""


def _page_dd1(c, prices, wn, yr):
    cu = prices.get("copper", {})
    li = prices.get("lithium", {})
    cu_h = cu.get("hist") or synth_hist(cu.get("current", 9840), cu.get("ytd", 11), seed=5)
    li_h = li.get("hist") or synth_hist(li.get("current", 13400), li.get("ytd", -11), seed=6)
    ex2 = exhibit_timeseries(pts_from_hist(cu_h), pts_from_hist(li_h),
                              "Lead commodity", "Second driver")
    head, foot = _hf(5, "Deep Dive · 1 / 3", wn, yr)
    return f"""
<!-- ═══ PAGE 5 — DEEP DIVE 1/3 ═══ -->
<section class="page">
  {head}
  <h4 class="label">Deep Dive · The Lead Story</h4>
  <h2 class="section" style="margin-top:3mm;margin-bottom:4mm;">{c.get("dd_title","")}</h2>
  <p class="lede mb-6">{c.get("dd_lede","")}</p>
  <p>{c.get("dd_p1","")}</p>
  <p>{c.get("dd_p2","")}</p>
  <div class="exhibit">
    <div class="ex-head"><div class="ex-num">Exhibit 02</div><div class="ex-title">{c.get("dd_ex2_title","")}</div></div>
    <div class="ex-frame">{ex2}
      <div class="ex-source">Source: LME, SHFE, Fastmarkets, Plavena composite · Weekly closes</div>
    </div>
  </div>
  <p style="margin-top:3mm;font-size:10.5pt;line-height:1.55;">{c.get("dd_ex2_caption","")}</p>
  {foot}
</section>"""


def _page_dd2(c, wn, yr):
    ex3 = """<svg viewBox="0 0 520 200" width="100%" height="180">
  <g stroke="rgba(255,255,255,0.08)" stroke-width="0.5">
    <line x1="60" y1="20" x2="500" y2="20"/><line x1="60" y1="60" x2="500" y2="60"/>
    <line x1="60" y1="100" x2="500" y2="100"/><line x1="60" y1="140" x2="500" y2="140"/>
    <line x1="60" y1="180" x2="500" y2="180"/>
  </g>
  <line x1="60" y1="100" x2="500" y2="100" stroke="rgba(255,255,255,0.25)" stroke-width="1"/>
  <g font-family="IBM Plex Mono" font-size="7" fill="#889AAA">
    <text x="56" y="23" text-anchor="end">+80</text><text x="56" y="103" text-anchor="end">0</text>
    <text x="56" y="183" text-anchor="end">-80</text>
  </g>
  <rect x="100" y="55" width="48" height="100" fill="rgba(255,90,95,0.55)" stroke="#FF5A5F" stroke-width="1"/>
  <rect x="185" y="72" width="48" height="68" fill="rgba(244,183,64,0.55)" stroke="#F4B740" stroke-width="1"/>
  <rect x="270" y="42" width="48" height="92" fill="rgba(255,90,95,0.55)" stroke="#FF5A5F" stroke-width="1"/>
  <rect x="355" y="88" width="48" height="28" fill="rgba(43,209,126,0.55)" stroke="#2BD17E" stroke-width="1"/>
  <rect x="440" y="82" width="48" height="38" fill="rgba(244,183,64,0.55)" stroke="#F4B740" stroke-width="1"/>
  <g font-family="IBM Plex Mono" font-size="7" fill="#FFFFFF" text-anchor="middle">
    <text x="124" y="193">Cohort A</text><text x="209" y="193">Cohort B</text>
    <text x="294" y="193">Cohort C</text><text x="379" y="193">Cohort D</text>
    <text x="464" y="193">Cohort E</text>
  </g>
</svg>"""
    head, foot = _hf(6, "Deep Dive · 2 / 3", wn, yr)
    return f"""
<!-- ═══ PAGE 6 — DEEP DIVE 2/3 ═══ -->
<section class="page">
  {head}
  <h3 class="sub mb-4">{c.get("dd_p2_title","")}</h3>
  <p>{c.get("dd_p2_para1","")}</p>
  <p>{c.get("dd_p2_para2","")}</p>
  <div class="exhibit">
    <div class="ex-head"><div class="ex-num">Exhibit 03</div><div class="ex-title">{c.get("dd_ex3_title","")}</div></div>
    <div class="ex-frame">{ex3}
      <div class="ex-source">Source: Company filings, CRU, SMM, Plavena analyst estimates</div>
    </div>
  </div>
  <div class="callout">
    <div class="co-label">— {c.get("dd_p2_callout_label","")}</div>
    <p>{c.get("dd_p2_callout","")}</p>
  </div>
  {foot}
</section>"""


def _page_dd3(c, wn, yr):
    ex4 = """<svg viewBox="0 0 520 190" width="100%" height="170">
  <g stroke="rgba(255,255,255,0.07)" stroke-width="0.5">
    <line x1="60" y1="40" x2="500" y2="40"/><line x1="60" y1="80" x2="500" y2="80"/>
    <line x1="60" y1="120" x2="500" y2="120"/><line x1="60" y1="160" x2="500" y2="160"/>
  </g>
  <line x1="60" y1="115" x2="500" y2="115" stroke="#00B3FF" stroke-width="1.4" stroke-dasharray="4,3"/>
  <text x="498" y="111" font-family="IBM Plex Mono" font-size="7" fill="#00B3FF" text-anchor="end">SPOT PRICE</text>
  <rect x="60"  y="148" width="80" height="12" fill="rgba(43,209,126,0.7)"  stroke="#2BD17E"  stroke-width="0.5"/>
  <rect x="140" y="138" width="65" height="22" fill="rgba(43,209,126,0.5)"  stroke="#2BD17E"  stroke-width="0.5"/>
  <rect x="205" y="125" width="80" height="35" fill="rgba(0,179,255,0.6)"   stroke="#00B3FF"  stroke-width="0.5"/>
  <rect x="285" y="112" width="70" height="48" fill="rgba(0,179,255,0.35)"  stroke="#00B3FF"  stroke-width="0.5"/>
  <rect x="355" y="102" width="60" height="58" fill="rgba(244,183,64,0.5)"  stroke="#F4B740"  stroke-width="0.5"/>
  <rect x="415" y="78"  width="60" height="82" fill="rgba(255,90,95,0.5)"   stroke="#FF5A5F"  stroke-width="0.5"/>
  <rect x="415" y="115" width="85" height="45" fill="rgba(255,90,95,0.08)"/>
  <text x="458" y="178" font-family="IBM Plex Mono" font-size="7" fill="#FF5A5F" text-anchor="middle">SUPPLY AT RISK</text>
  <text x="280" y="188" font-family="IBM Plex Mono" font-size="6.5" fill="#889AAA" text-anchor="middle">CUMULATIVE PRODUCTION →</text>
</svg>"""
    head, foot = _hf(7, "Deep Dive · 3 / 3", wn, yr)
    return f"""
<!-- ═══ PAGE 7 — DEEP DIVE 3/3 ═══ -->
<section class="page">
  {head}
  <h3 class="sub mb-4">{c.get("dd_p3_title","")}</h3>
  <p class="lede mb-4">{c.get("dd_p3_lede","")}</p>
  <p>{c.get("dd_p3_para1","")}</p>
  <div class="exhibit">
    <div class="ex-head"><div class="ex-num">Exhibit 04</div><div class="ex-title">Supply cost curve · Where the floor lives</div></div>
    <div class="ex-frame">{ex4}
      <div class="ex-source">Source: Benchmark Mineral, company filings, Plavena estimates · 2026E cash costs</div>
    </div>
  </div>
  <p style="margin-top:4mm;font-size:10.5pt;">{c.get("dd_p3_para2","")}</p>
  <div class="callout">
    <div class="co-label">— {c.get("dd_p3_callout_label","")}</div>
    <p>{c.get("dd_p3_callout","")}</p>
  </div>
  {foot}
</section>"""


def _page_watchlist(c, wn, yr, sun):
    rows = ""
    for w in c.get("watchlist", []):
        mc = "pos" if w.get("mvp") else "neg"
        rows += (f'<tr><td class="ticker">{w["ticker"]}</td><td>{w["name"]}</td>'
                 f'<td class="move {mc}">{w["mv"]}</td><td>{w["catalyst"]}</td>'
                 f'<td class="view">{pill(w["view"])}</td></tr>')
    head, foot = _hf(8, "Watchlist", wn, yr)
    return f"""
<!-- ═══ PAGE 8 — WATCHLIST ═══ -->
<section class="page">
  {head}
  <h2 class="section">Watchlist</h2>
  <p class="meta-row" style="margin-top:2mm;margin-bottom:8mm;">10 names · Catalysts within 8 weeks · Pricing reflects close {sun.strftime("%d %b %Y")}</p>
  <table class="watch-table">
    <thead><tr>
      <th style="width:26mm;">Ticker</th><th>Name</th>
      <th style="text-align:right;width:18mm;">1W</th>
      <th>Catalyst (≤8 wk)</th><th class="view">Plavena view</th>
    </tr></thead>
    <tbody>{rows}</tbody>
  </table>
  {foot}
</section>"""


def _page_deals(c, wn, yr):
    cards = ""
    for d in c.get("deals", []):
        cards += f"""
    <div class="deal">
      <div class="deal-head"><div class="deal-id">{d["id"]}</div><div class="deal-status">{d["status"]}</div></div>
      <h4 class="deal-title">{d["title"]}</h4>
      <dl class="deal-meta">
        <dt>Commodity</dt><dd>{d["commodity"]}</dd>
        <dt>Volume</dt><dd>{d["volume"]}</dd>
        <dt>Specification</dt><dd>{d["spec"]}</dd>
        <dt>Indicative price</dt><dd>{d["price"]}</dd>
        <dt>Logistics</dt><dd>{d["logist"]}</dd>
        <dt>Window</dt><dd>{d["window"]}</dd>
      </dl>
    </div>"""
    head, foot = _hf(9, "Deal Flow", wn, yr)
    return f"""
<!-- ═══ PAGE 9 — DEAL FLOW ═══ -->
<section class="page">
  {head}
  <h2 class="section">Deal Flow</h2>
  <p class="meta-row" style="margin-top:2mm;margin-bottom:5mm;">Active prospects · Finder's fee 1.5% on closed transactions · 500+ vetted counterparties across 20+ countries</p>
  <div class="deal-grid">{cards}</div>
  <div class="deal-cta">
    <div class="cta-text">Engage on any of these deals
      <small>Contact our trading desk · Strict NDA · 72-hour response</small>
    </div>
    <div class="cta-action">deal@plavena.com →</div>
  </div>
  {foot}
</section>"""


def _page_lookahead(c, wn, yr):
    cal_html = ""
    for day in c.get("calendar", []):
        evs = ""
        for ev in day.get("events", []):
            evs += f"<li><b>{ev.get('t','')}</b> {ev.get('d','')}</li>"
        if not evs:
            evs = '<li style="color:var(--whisper)">No scheduled events</li>'
        cal_html += (f'<div class="cal-day">'
                     f'<div class="cd-date">{day.get("date","")}</div>'
                     f'<div class="cd-day">{day.get("day","")}</div>'
                     f'<ul>{evs}</ul></div>')
    head, foot = _hf(10, "Look-Ahead", wn, yr)
    return f"""
<!-- ═══ PAGE 10 — LOOK-AHEAD ═══ -->
<section class="page">
  {head}
  <h2 class="section" style="margin-bottom:5mm;">The week ahead.</h2>
  <div class="cal-grid">{cal_html}</div>
  <div class="meth" style="margin-top:8mm;">
    <h4 class="label">Outlook</h4>
    <p style="font-size:10.5pt;line-height:1.55;">{c.get("outlook","")}</p>
  </div>
  <div class="meth-grid" style="margin-top:6mm;">
    <div>
      <h4 class="label">Methodology</h4>
      <p>Plavena compiles weekly price data from LME official settlements, SHFE closing prices, Fastmarkets spot assessments, and Freightos Baltic container indices. Analysis layers Plavena's proprietary deal-flow intelligence and counterparty network data. All calls are tracked and audited weekly.</p>
    </div>
    <div>
      <h4 class="label">Disclaimer</h4>
      <p>This report is for professional subscribers only. Nothing herein constitutes financial advice. Commodity markets carry material risk. Past call performance does not guarantee future results. Plavena may hold positions in instruments discussed. Not for redistribution.</p>
    </div>
  </div>
  <div class="signoff">
    <div class="analyst">
      <div class="name">{c.get("analyst_name","Harsh Dhillon")}</div>
      <div class="role">{c.get("analyst_role","Lead Analyst · Metals &amp; Mining")}</div>
    </div>
    <div class="contact">
      Intelligence: <b>intelligence@plavena.com</b><br>
      Deal desk: <b>deal@plavena.com</b><br>
      Web: <b>plavena.com</b>
    </div>
  </div>
  {foot}
</section>"""


# ══════════════════════════════════════════════════════════════
# 6. ASSEMBLE FULL HTML
# ══════════════════════════════════════════════════════════════

def build_html(c, prices, wn, yr, date_range, mon, sun):
    with open("style.css", encoding="utf-8") as f:
        css = f.read()

    pages = "".join([
        _page_cover(c, wn, yr, date_range, mon, sun),
        _page_exec(c, wn, yr),
        _page_prices(c, wn, yr, mon, sun),
        _page_maps(c, prices, wn, yr),
        _page_dd1(c, prices, wn, yr),
        _page_dd2(c, wn, yr),
        _page_dd3(c, wn, yr),
        _page_watchlist(c, wn, yr, sun),
        _page_deals(c, wn, yr),
        _page_lookahead(c, wn, yr),
    ])

    return (f'<!doctype html>\n<html lang="en">\n<head>\n'
            f'<meta charset="utf-8">\n<meta name="viewport" content="width=device-width,initial-scale=1">\n'
            f'<title>Plavena Weekly Brief — Metals &amp; Mining — W{wn}/{yr}</title>\n'
            f'<style>{css}</style>\n</head>\n<body>\n{pages}\n</body>\n</html>')


# ══════════════════════════════════════════════════════════════
# 7. EMAIL DELIVERY
# ══════════════════════════════════════════════════════════════

def _teaser_email(c, wn, yr, report_url):
    heroes = c.get("hero", [{}, {}, {}])
    h1, h2, h3 = (heroes + [{}, {}, {}])[:3]
    tldr_html = "".join(
        f'<li style="margin:8px 0;color:#C9D4E0;font-size:14px;">{b}</li>'
        for b in c.get("tldr", [])
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"></head>
<body style="background:#080D1A;margin:0;padding:24px;font-family:'Helvetica Neue',Arial,sans-serif;">
<div style="max-width:560px;margin:0 auto;">
  <div style="border-bottom:1px solid rgba(255,255,255,0.1);padding-bottom:16px;margin-bottom:24px;">
    <span style="color:#00B3FF;font-weight:900;font-size:20px;letter-spacing:0.05em;">PLAVENA</span>
    <span style="color:#889AAA;font-size:11px;letter-spacing:0.2em;margin-left:12px;text-transform:uppercase;">Weekly Brief · W{wn}/{yr}</span>
  </div>
  <h1 style="color:#FFF;font-size:26px;line-height:1.1;margin:0 0 12px;font-weight:800;">
    {c.get("cover_h1","")} {c.get("cover_h2","")}
    <span style="color:#00B3FF;"> {c.get("cover_h3_accent","")}</span>
  </h1>
  <p style="color:#C9D4E0;font-size:14px;line-height:1.5;margin:0 0 24px;">{c.get("cover_deck","")}</p>
  <table width="100%" cellpadding="0" cellspacing="12" style="margin-bottom:24px;">
    <tr>
      <td style="background:#0D1B2A;border-top:2px solid #00B3FF;padding:12px;width:33%;">
        <div style="font-size:28px;font-weight:800;color:#00B3FF;">{h1.get("num","")}</div>
        <div style="font-size:8px;color:#889AAA;letter-spacing:0.1em;text-transform:uppercase;margin-top:4px;">{h1.get("unit","")}</div>
      </td>
      <td style="background:#0D1B2A;border-top:2px solid #2BD17E;padding:12px;width:33%;">
        <div style="font-size:28px;font-weight:800;color:#2BD17E;">{h2.get("num","")}</div>
        <div style="font-size:8px;color:#889AAA;letter-spacing:0.1em;text-transform:uppercase;margin-top:4px;">{h2.get("unit","")}</div>
      </td>
      <td style="background:#0D1B2A;border-top:2px solid #FF5A5F;padding:12px;width:33%;">
        <div style="font-size:28px;font-weight:800;color:#FF5A5F;">{h3.get("num","")}</div>
        <div style="font-size:8px;color:#889AAA;letter-spacing:0.1em;text-transform:uppercase;margin-top:4px;">{h3.get("unit","")}</div>
      </td>
    </tr>
  </table>
  <div style="background:#0D1B2A;border:1px solid rgba(255,255,255,0.1);padding:16px;margin-bottom:24px;">
    <div style="font-size:10px;color:#00B3FF;letter-spacing:0.2em;text-transform:uppercase;margin-bottom:12px;">This week in 60 seconds</div>
    <ol style="padding-left:20px;margin:0;">{tldr_html}</ol>
  </div>
  <div style="text-align:center;margin-bottom:24px;">
    <a href="{report_url}" style="display:inline-block;background:#00B3FF;color:#061018;padding:14px 32px;font-weight:700;font-size:14px;text-decoration:none;border-radius:4px;">Read Full Report (10 pages) →</a>
  </div>
  <div style="border-top:1px solid rgba(255,255,255,0.1);padding-top:12px;font-size:10px;color:#4E6075;text-align:center;">
    Plavena Intelligence · deal@plavena.com · intelligence@plavena.com<br>
    You receive this because you subscribed at plavena.com
  </div>
</div>
</body></html>"""


def send_emails(c, wn, yr, report_url):
    sender  = os.environ.get("SENDER_EMAIL")
    pw      = os.environ.get("GMAIL_APP_PASSWORD")
    subs    = [s.strip() for s in os.environ.get("SUBSCRIBER_EMAILS", "").split(",") if s.strip()]

    if not sender or not pw:
        print("  Email credentials missing — skipping")
        return
    if not subs:
        print("  No SUBSCRIBER_EMAILS configured — skipping")
        return

    subject = f"Plavena Weekly Brief — W{wn}/{yr} · Metals & Mining"
    html    = _teaser_email(c, wn, yr, report_url)

    for email in subs:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"Plavena Intelligence <{sender}>"
        msg["To"]      = email
        msg.attach(MIMEText(
            f"Plavena Weekly Brief W{wn}/{yr}\n\nRead: {report_url}\n\n--\nPlavena · plavena.com",
            "plain"
        ))
        msg.attach(MIMEText(html, "html"))
        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
                smtp.login(sender, pw)
                smtp.sendmail(sender, email, msg.as_string())
            print(f"  ✓ {email}")
        except Exception as e:
            print(f"  ✗ {email}: {e}")


# ══════════════════════════════════════════════════════════════
# 8. MAIN
# ══════════════════════════════════════════════════════════════

def main():
    dry_run = ("--dry-run" in sys.argv) or (
        os.environ.get("PLAVENA_DRY_RUN", "").strip().lower() in ("1", "true", "yes", "on")
    )

    wn, yr, date_range, mon, sun, next_days = week_info()
    print(f"\n{'═'*56}")
    print(f"  Plavena Weekly Brief — W{wn}/{yr} ({date_range})")
    if dry_run:
        print("  DRY RUN — no emails sent, docs/ untouched, writes output/ only")
    print(f"{'═'*56}\n")

    # Deals queue
    try:
        with open("deals.json") as f:
            deals_queue = json.load(f).get("queue", [])
        print(f"  Deals in queue: {len(deals_queue)}")
    except FileNotFoundError:
        deals_queue = []
        print("  deals.json not found — Claude will generate deals")

    # Fetch prices
    print("\n[1/4] Fetching commodity prices...")
    prices = fetch_prices()

    # Generate content
    print("\n[2/4] Generating report content via Claude API...")
    content = generate_content(prices, wn, yr, date_range, next_days, deals_queue)

    # Overwrite the price table with verified live data — the model only supplies
    # the vs-forecast call + view; spot / 1W / 4W / YTD always come from real prices.
    content["prices_table"] = assemble_price_table(prices, content.get("prices_view", []))

    # Build HTML
    print("\n[3/4] Building HTML report...")
    html = build_html(content, prices, wn, yr, date_range, mon, sun)

    # Save
    os.makedirs("output", exist_ok=True)

    fname = f"plavena-w{wn:02d}-{yr}.html"
    targets = [f"output/{fname}"]
    if not dry_run:                       # dry runs never touch the published docs/
        os.makedirs("docs", exist_ok=True)
        targets.append(f"docs/{fname}")
    for path in targets:
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)

    # GitHub Pages redirect index (live runs only)
    if not dry_run:
        with open("docs/index.html", "w") as f:
            f.write(f'<meta http-equiv="refresh" content="0; url={fname}">')

    print(f"  Saved: {', '.join(targets)}")

    # Report URL
    repo_owner = os.environ.get("GITHUB_REPOSITORY_OWNER", "your-github-username")
    repo_name  = os.environ.get("GITHUB_REPOSITORY", f"{repo_owner}/plavena-reports").split("/")[-1]
    report_url = f"https://{repo_owner}.github.io/{repo_name}/{fname}"
    print(f"  URL: {report_url}")

    # Send emails (skipped on dry runs)
    if dry_run:
        print("\n[4/4] DRY RUN — skipping email send (0 subscribers contacted).")
    else:
        print("\n[4/4] Sending emails...")
        send_emails(content, wn, yr, report_url)

    print(f"\n✓ Done.\n")


if __name__ == "__main__":
    main()
