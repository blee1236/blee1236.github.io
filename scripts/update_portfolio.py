"""Build portfolio.html: beta-adjusted alpha tracker for Bennett's personal stock account.

Privacy: the page shows PERCENTAGES ONLY (returns, weights, beta, alpha). Dollar amounts,
share counts, cost basis and account details are never written to the page or the repo.

Holdings come from the HOLDINGS_CSV environment variable (a GitHub Actions secret) or, for
local runs, a gitignored holdings.csv in the repo root. One line per tax lot:

    ticker,acquisition_date,cost
    AMD,2019-08-30,633.75

Method (single-factor CAPM, total return):
  * Prices: Yahoo Finance daily adjusted closes (dividends reinvested). Benchmark is the
    S&P 500 total-return index (^SP500TR); risk-free rate is the 13-week T-bill (^IRX).
  * Beta: weekly excess returns vs the S&P 500 over each position's holding window
    (minimum 2 years of history).
  * Alpha per lot: actual return since purchase minus the return its beta predicts,
    compounded daily: prod(1 + rf + beta * (r_mkt - rf)) - 1.
  * Annualized alpha: the constant yearly rate that, applied on top of every lot's
    beta-predicted path, reproduces the actual result (cost-weighted).

Run from the repo root:  python scripts/update_portfolio.py
Uses only the standard library (certifi is used if installed).
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import ssl
import sys
import time
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "portfolio.html"
LOCAL_HOLDINGS = ROOT / "holdings.csv"
ET = ZoneInfo("America/New_York")

MARKET = "^SP500TR"
RISK_FREE = "^IRX"
COMPARE = "QQQ"
MIN_BETA_DAYS = 504  # ~2 years of trading days
MIN_YEARS_FOR_ANNUALIZED = 1.5

try:
    import certifi

    CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    CTX = ssl.create_default_context()

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"


# ---------------------------------------------------------------- holdings
def load_holdings() -> list[dict]:
    text = os.environ.get("HOLDINGS_CSV", "").strip()
    if not text and LOCAL_HOLDINGS.exists():
        text = LOCAL_HOLDINGS.read_text()
    if not text:
        sys.exit("No holdings: set HOLDINGS_CSV or create holdings.csv (ticker,date,cost).")
    lots = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.lower().startswith("ticker"):
            continue
        tkr, date, cost = [x.strip() for x in line.split(",")[:3]]
        lots.append({"t": tkr.upper(), "d": dt.date.fromisoformat(date), "c": float(cost)})
    return lots


# ---------------------------------------------------------------- prices
def fetch_series(symbol: str, start: dt.date) -> dict[dt.date, float]:
    """Daily total-return price series {date: adjusted close}, including today's live bar."""
    p1 = int(dt.datetime(start.year, start.month, start.day, tzinfo=dt.timezone.utc).timestamp())
    p2 = int(time.time()) + 86400
    sym = urllib.request.quote(symbol)
    last_err = None
    for host in ("query1", "query2"):
        url = (f"https://{host}.finance.yahoo.com/v8/finance/chart/{sym}"
               f"?period1={p1}&period2={p2}&interval=1d&events=div%2Csplit")
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
                    res = json.load(r)["chart"]["result"][0]
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                time.sleep(2 + 3 * attempt)
        else:
            continue
        break
    else:
        raise RuntimeError(f"Could not fetch {symbol}: {last_err}")

    ts = res.get("timestamp") or []
    q = res["indicators"]["quote"][0]
    close = q.get("close") or [None] * len(ts)
    adj = (res["indicators"].get("adjclose") or [{}])[0].get("adjclose") or close
    out: dict[dt.date, float] = {}
    factor = 1.0  # adj/close ratio, carried forward for bars where adjclose is missing (live bar)
    for t, c, a in zip(ts, close, adj):
        d = dt.datetime.fromtimestamp(t, ET).date()
        if a is not None and c:
            factor = a / c
            out[d] = a
        elif c is not None:
            out[d] = c * factor
    live = res.get("meta", {}).get("regularMarketPrice")
    live_t = res.get("meta", {}).get("regularMarketTime")
    if live and live_t:
        d = dt.datetime.fromtimestamp(live_t, ET).date()
        out[d] = live * factor
    return out


# ---------------------------------------------------------------- math
def ffill(series: dict, dates: list) -> list:
    out, last = [], None
    for d in dates:
        if d in series:
            last = series[d]
        out.append(last)
    return out


def estimate_beta(stock: list, mkt: list, rf: list, i_start: int) -> tuple[float, float]:
    """Weekly (5-trading-day) excess-return regression of stock on market, ending at the last date."""
    n = len(mkt)
    idx = [i for i in range(n - 1, i_start - 1, -5)][::-1]
    idx = [i for i in idx if stock[i] is not None]
    xs, ys = [], []
    for a, b in zip(idx, idx[1:]):
        rfc = 1.0
        for i in range(a + 1, b + 1):
            rfc *= 1 + rf[i]
        rfc -= 1
        ys.append(stock[b] / stock[a] - 1 - rfc)
        xs.append(mkt[b] / mkt[a] - 1 - rfc)
    if len(xs) < 8:
        return 1.0, 0.0
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    beta = cov / vx if vx else 1.0
    r2 = cov * cov / (vx * vy) if vx and vy else 0.0
    return beta, r2


def solve_annual_alpha(lots: list[dict]) -> float | None:
    """Constant yearly alpha a with sum c(1+E)(1+a)^y == sum c(1+A)."""
    lots = [l for l in lots if l["yrs"] > 0]
    if not lots:
        return None
    target = sum(l["c"] * (1 + l["act"]) for l in lots)
    lo, hi = -0.95, 5.0
    for _ in range(200):
        a = (lo + hi) / 2
        v = sum(l["c"] * (1 + l["exp"]) * (1 + a) ** l["yrs"] for l in lots)
        lo, hi = (a, hi) if v < target else (lo, a)
    return (lo + hi) / 2


def compute(lots: list[dict], fetch=fetch_series) -> dict:
    start = min(l["d"] for l in lots) - dt.timedelta(days=800)
    mkt_s = fetch(MARKET, start)
    dates = sorted(mkt_s)
    mkt = ffill(mkt_s, dates)
    irx = ffill(fetch(RISK_FREE, start), dates)
    rf = [((x if x is not None else 4.0) / 100) / 252 for x in irx]
    cmp_ = ffill(fetch(COMPARE, start), dates)
    pos = {d: i for i, d in enumerate(dates)}
    last = len(dates) - 1
    as_of = dates[-1]

    def index_of(d: dt.date) -> int:
        while d not in pos:
            d -= dt.timedelta(days=1)
        return pos[d]

    tickers = sorted({l["t"] for l in lots})
    rows, all_lots = [], []
    for t in tickers:
        px = ffill(fetch(t, start), dates)
        tl = [l for l in lots if l["t"] == t]
        first_i = min(index_of(l["d"]) for l in tl)
        beta, r2 = estimate_beta(px, mkt, rf, min(first_i, max(0, last - MIN_BETA_DAYS)))
        agg = {"c": 0.0, "v": 0.0, "act": 0.0, "exp": 0.0, "mkt": 0.0, "cmp": 0.0, "cy": 0.0}
        for l in tl:
            i0 = index_of(l["d"])
            if px[i0] is None:
                continue
            e = 1.0
            for i in range(i0 + 1, last + 1):
                rm = mkt[i] / mkt[i - 1] - 1
                e *= 1 + rf[i] + beta * (rm - rf[i])
            act = px[last] / px[i0] - 1
            yrs = (last - i0) / 252
            lot = {"c": l["c"], "act": act, "exp": e - 1, "yrs": yrs}
            all_lots.append(lot)
            agg["c"] += l["c"]
            agg["v"] += l["c"] * (1 + act)
            agg["act"] += l["c"] * act
            agg["exp"] += l["c"] * (e - 1)
            agg["mkt"] += l["c"] * (mkt[last] / mkt[i0] - 1)
            agg["cmp"] += l["c"] * (cmp_[last] / cmp_[i0] - 1)
            agg["cy"] += l["c"] * yrs
            lot["t"] = t
        if not agg["c"]:
            continue
        c = agg["c"]
        yrs = agg["cy"] / c
        ann = None
        if yrs >= MIN_YEARS_FOR_ANNUALIZED:
            ann = ((1 + agg["act"] / c) / (1 + agg["exp"] / c)) ** (1 / yrs) - 1
        rows.append({
            "t": t, "beta": beta, "r2": r2, "yrs": yrs, "c": c, "v": agg["v"],
            "first": min(l["d"] for l in tl),
            "ret": agg["act"] / c, "spx": agg["mkt"] / c, "cmp": agg["cmp"] / c,
            "pred": agg["exp"] / c, "alpha": (agg["act"] - agg["exp"]) / c, "ann": ann,
        })

    C = sum(r["c"] for r in rows)
    V = sum(r["v"] for r in rows)
    for r in rows:
        r["w"] = r["v"] / V
    long_lots = [l for l in all_lots if l["yrs"] >= 2]
    summary = {
        "ret": sum(r["ret"] * r["c"] for r in rows) / C,
        "spx": sum(r["spx"] * r["c"] for r in rows) / C,
        "cmp": sum(r["cmp"] * r["c"] for r in rows) / C,
        "pred": sum(r["pred"] * r["c"] for r in rows) / C,
        "alpha": sum(r["alpha"] * r["c"] for r in rows) / C,
        "beta": sum(r["beta"] * r["w"] for r in rows),
        "ann_all": solve_annual_alpha(all_lots),
        "ann_long": solve_annual_alpha(long_lots),
        "long_share": (sum(l["c"] for l in long_lots) / C) if C else 0,
        "n": len(rows),
        "beat_spx": sum(1 for r in rows if r["ret"] >= r["spx"]),
        "beat_cmp": sum(1 for r in rows if r["ret"] >= r["cmp"]),
        "pos_alpha": sum(1 for r in rows if r["alpha"] > 0),
    }
    rows.sort(key=lambda r: -r["alpha"] * r["c"])  # rank by contribution to total alpha
    return {"rows": rows, "summary": summary, "as_of": as_of}


# ---------------------------------------------------------------- page
def pct(x: float | None, digits: int = 0, sign: bool = True) -> str:
    if x is None:
        return "—"
    s = f"{x * 100:+,.{digits}f}%" if sign else f"{x * 100:,.{digits}f}%"
    return s.replace("-", "−")


def cls(x: float | None) -> str:
    if x is None or abs(x) < 1e-9:
        return "flat"
    return "up" if x > 0 else "down"


def render(data: dict, generated: dt.datetime) -> str:
    s, rows = data["summary"], data["rows"]
    trs = []
    for r in rows:
        beta_s = f"{r['beta']:.2f}".replace("-", "−")
        trs.append(
            "<tr>"
            f"<th scope=\"row\">{html.escape(r['t'])}</th>"
            f"<td>{pct(r['w'], 1, sign=False)}</td>"
            f"<td>{r['first']:%b %Y}</td>"
            f"<td>{r['yrs']:.1f}</td>"
            f"<td class=\"{cls(r['ret'])}\">{pct(r['ret'])}</td>"
            f"<td>{pct(r['spx'])}</td>"
            f"<td>{pct(r['cmp'])}</td>"
            f"<td>{beta_s}{'<sup>†</sup>' if r['r2'] < 0.05 else ''}</td>"
            f"<td>{pct(r['pred'])}</td>"
            f"<td class=\"{cls(r['alpha'])}\"><b>{pct(r['alpha'])}</b></td>"
            f"<td class=\"{cls(r['ann'])}\">{pct(r['ann'], 1)}</td>"
            "</tr>"
        )
    stamp = generated.astimezone(ET).strftime("%b %-d, %Y at %-I:%M %p ET")
    kpis = [
        ("Return on cost", pct(s["ret"]), f"S&amp;P 500 same dates {pct(s['spx'])} · QQQ {pct(s['cmp'])}", cls(s["ret"] - s["spx"])),
        ("Portfolio beta", f"{s['beta']:.2f}", "vs S&amp;P 500, value-weighted", "flat"),
        ("Alpha per year, all positions", pct(s["ann_all"], 1), "beta-adjusted, cost- and time-weighted", cls(s["ann_all"])),
        ("Alpha per year, held 2+ years", pct(s["ann_long"], 1), f"{pct(s['long_share'], 0, sign=False)} of cost; the most reliable figure", cls(s["ann_long"])),
        ("Hit rate", f"{s['pos_alpha']} of {s['n']}", f"positive alpha · {s['beat_spx']} beat the S&amp;P · {s['beat_cmp']} beat QQQ", "flat"),
    ]
    kpi_html = "\n".join(
        f'<div class="kpi"><div class="l">{l}</div><div class="v {c}">{v}</div><div class="s">{sub}</div></div>'
        for l, v, sub, c in kpis
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Track Record — Bennett Lee</title>
  <meta name="description" content="Beta-adjusted performance of Bennett Lee's personal stock portfolio versus the S&amp;P 500 and QQQ.">
  <style>
    :root {{
      --columbia: #B9D9EB; --columbia-soft: #E6F1F8; --navy: #1D4F91; --deep: #0C2340;
      --bg: #F7FAFC; --card: #FFFFFF; --text: #1A2B3C; --muted: #55697D;
      --up: #0f7a45; --down: #b8232f; --radius: 14px; --shadow: 0 2px 10px rgba(12, 35, 64, 0.07);
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      background: var(--bg); color: var(--text); line-height: 1.55; }}
    a {{ color: var(--navy); }}
    a:focus-visible {{ outline: 3px solid var(--navy); outline-offset: 2px; }}
    nav {{ position: sticky; top: 0; z-index: 10; background: var(--deep); border-bottom: 3px solid var(--columbia); }}
    .nav-inner {{ max-width: 1100px; margin: 0 auto; padding: 0.85rem 1.5rem; display: flex; align-items: center;
      justify-content: space-between; gap: 1rem; flex-wrap: wrap; }}
    .brand {{ color: #fff; font-weight: 700; text-decoration: none; letter-spacing: 0.02em; }}
    .brand span {{ color: var(--columbia); }}
    .nav-links {{ display: flex; gap: 1.4rem; list-style: none; flex-wrap: wrap; }}
    .nav-links a {{ color: #DCE9F3; text-decoration: none; font-size: 0.95rem; font-weight: 500;
      padding-bottom: 2px; border-bottom: 2px solid transparent; }}
    .nav-links a:hover, .nav-links a.active {{ color: #fff; border-bottom-color: var(--columbia); }}
    .nav-links a.nav-cta {{ background: var(--columbia); color: var(--deep); font-weight: 700;
      padding: 0.25rem 0.8rem; border-radius: 999px; border-bottom: none; }}
    main {{ max-width: 1100px; margin: 0 auto; padding: 2.2rem 1.5rem 3rem; }}
    h1 {{ color: var(--deep); font-size: clamp(1.8rem, 4vw, 2.4rem); line-height: 1.15; }}
    .lede {{ color: #23384D; max-width: 760px; margin: 0.5rem 0 0.4rem; }}
    .stamp {{ color: var(--muted); font-size: 0.88rem; }}
    .num, td, .kpi .v {{ font-variant-numeric: tabular-nums; }}
    .up {{ color: var(--up); }} .down {{ color: var(--down); }} .flat {{ color: var(--deep); }}
    .kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 0.9rem; margin: 1.6rem 0; }}
    .kpi {{ background: var(--card); border-radius: var(--radius); box-shadow: var(--shadow); padding: 1rem 1.1rem;
      border-top: 4px solid var(--navy); }}
    .kpi .l {{ font-size: 0.78rem; font-weight: 700; color: var(--navy); text-transform: uppercase; letter-spacing: 0.06em; }}
    .kpi .v {{ font-size: 1.7rem; font-weight: 700; margin: 0.15rem 0; }}
    .kpi .s {{ font-size: 0.85rem; color: var(--muted); }}
    .card {{ background: var(--card); border-radius: var(--radius); box-shadow: var(--shadow); padding: 1.3rem 1.4rem;
      margin-bottom: 1.1rem; border-left: 5px solid var(--columbia); }}
    .card h2 {{ font-size: 1.15rem; color: var(--deep); margin-bottom: 0.6rem; }}
    .tablewrap {{ overflow-x: auto; }}
    table {{ border-collapse: collapse; width: 100%; min-width: 860px; font-size: 0.92rem; }}
    th, td {{ padding: 0.5rem 0.6rem; text-align: right; white-space: nowrap; border-bottom: 1px solid #E3EBF2; }}
    thead th {{ font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); font-weight: 700;
      vertical-align: bottom; white-space: normal; }}
    tbody th {{ text-align: left; color: var(--deep); }}
    thead th:first-child {{ text-align: left; }}
    tbody tr:hover {{ background: var(--columbia-soft); }}
    .notes {{ color: var(--muted); font-size: 0.9rem; }}
    .notes li {{ margin: 0 0 0.35rem 1.1rem; }}
    footer {{ background: var(--deep); color: #C9DAE8; text-align: center; padding: 2rem 1.5rem; font-size: 0.9rem; }}
    footer a {{ color: var(--columbia); }}
  </style>
</head>
<body>
  <nav aria-label="Main">
    <div class="nav-inner">
      <a class="brand" href="index.html">Bennett <span>Lee</span></a>
      <ul class="nav-links">
        <li><a href="index.html#education">Education</a></li>
        <li><a href="index.html#experience">Experience</a></li>
        <li><a href="research.html">Research</a></li>
        <li><a class="active" href="portfolio.html" aria-current="page">Track Record</a></li>
        <li><a href="dashboard.html">Dashboard</a></li>
        <li><a href="movers.html">Movers</a></li>
        <li><a href="jobs.html">Jobs</a></li>
        <li><a class="nav-cta" href="Lee_Bennett_Resume_2026.pdf" target="_blank" rel="noopener">Resume ↓</a></li>
      </ul>
    </div>
  </nav>

  <main>
    <h1>Personal portfolio track record</h1>
    <p class="lede">How the stocks I currently own have done since I bought them, measured against the market and adjusted
      for how much market risk each one carries. Alpha is the return left over after beta is accounted for.</p>
    <p class="stamp">Updated {stamp} · refreshes every 30 minutes while the market is open</p>

    <section class="kpis" aria-label="Summary">
{kpi_html}
    </section>

    <section class="card" aria-labelledby="pos-h">
      <h2 id="pos-h">By position <span class="stamp">(ranked by contribution to total alpha)</span></h2>
      <div class="tablewrap">
        <table>
          <thead>
            <tr>
              <th scope="col">Ticker</th>
              <th scope="col">Weight</th>
              <th scope="col">First bought</th>
              <th scope="col">Years held</th>
              <th scope="col">Return</th>
              <th scope="col">S&amp;P 500, same dates</th>
              <th scope="col">QQQ, same dates</th>
              <th scope="col">Beta</th>
              <th scope="col">Beta-predicted</th>
              <th scope="col">Alpha</th>
              <th scope="col">Alpha per year</th>
            </tr>
          </thead>
          <tbody>
            {"".join(trs)}
          </tbody>
        </table>
      </div>
    </section>

    <section class="card notes" aria-labelledby="m-h">
      <h2 id="m-h">How this is calculated</h2>
      <ul>
        <li>Every purchase is measured from its own buy date to today, using total returns (dividends reinvested) for
          the stocks, the S&amp;P 500 total-return index and QQQ. Weights are shares of current market value.</li>
        <li>Beta comes from weekly returns against the S&amp;P 500 over each position's holding period, with at least two
          years of history. The beta-predicted return compounds the T-bill rate plus beta times the market's excess return
          day by day; alpha is the actual return minus that.</li>
        <li>Alpha per year is only shown for positions held at least {MIN_YEARS_FOR_ANNUALIZED:g} years; annualizing a few
          months of returns mostly measures noise. <sup>†</sup> marks a beta with almost no statistical fit.</li>
        <li>This is a single-factor model: it adjusts for market risk, not for sector, size or momentum, so part of the
          alpha in semiconductors and other hot sectors may be a sector effect.</li>
        <li>Covers positions currently held in one personal brokerage account; closed positions, options and retirement
          accounts are excluded. Prices from Yahoo Finance and may be delayed.</li>
        <li>Personal record for illustration only. Not investment advice or a recommendation to buy or sell any security;
          I may change these positions at any time. Past performance does not guarantee future results.</li>
      </ul>
    </section>
  </main>

  <footer>
    © {generated.astimezone(ET):%Y} Bennett Lee · <a href="https://www.linkedin.com/in/bennettlee1/" target="_blank" rel="noopener">LinkedIn</a> · <a href="https://github.com/blee1236" target="_blank" rel="noopener">GitHub</a>
  </footer>
</body>
</html>
"""


def main() -> None:
    lots = load_holdings()
    data = compute(lots)
    OUT.write_text(render(data, dt.datetime.now(dt.timezone.utc)))
    s = data["summary"]
    print(f"Wrote {OUT.name}: {s['n']} positions, beta {s['beta']:.2f}, "
          f"alpha/yr {pct(s['ann_all'], 1)} (2y+: {pct(s['ann_long'], 1)})")


if __name__ == "__main__":
    main()
