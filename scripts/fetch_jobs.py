"""Build jobs.html: NYC investment management, equity research and hedge fund roles.

Pulls open roles from firms' public career-site feeds (Workday, Greenhouse and
JPMorgan's Oracle site), keeps New York roles that match buy-side / research
keywords, and writes a static jobs.html. Firms without a public feed (Goldman
Sachs, Citadel, D.E. Shaw, ...) are listed in a directory with links.

Optional: linkedin_jobs.json in the repo root (a list of
{title, firm, location, posted, url}) is shown in its own section. It is meant
to be filled by hand or by a supervised Claude in Chrome session, never by an
automated scraper.

Run from the repo root:  python scripts/fetch_jobs.py
Uses only the standard library (certifi is used if installed).
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import html
import json
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "jobs.html"
LINKEDIN = ROOT / "linkedin_jobs.json"

try:
    import certifi

    CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    CTX = ssl.create_default_context()

UA = "Mozilla/5.0 (compatible; personal-job-board/1.0)"

# ---------------------------------------------------------------- feeds
WORKDAY = [
    # firm, tenant, wd number, site, group
    ("Morgan Stanley", "ms", 5, "External", "Banks"),
    ("Citi", "citi", 5, "2", "Banks"),
    ("Bank of America", "ghr", 1, "Lateral-US", "Banks"),
    ("Wells Fargo", "wf", 1, "WellsFargoJobs", "Banks"),
    ("Barclays", "barclays", 3, "External_Career_Site_Barclays", "Banks"),
    ("Deutsche Bank", "db", 3, "DBWebSite", "Banks"),
    ("Mizuho", "mizuho", 1, "mizuhoamericas", "Banks"),
    ("Guggenheim", "guggenheim", 1, "Guggenheim_Careers", "Banks"),
    ("Houlihan Lokey", "hl", 1, "Campus", "Banks"),
    ("BlackRock", "blackrock", 1, "BlackRock_Professional", "Asset managers"),
    ("Fidelity", "fmr", 1, "FidelityCareers", "Asset managers"),
    ("Wellington", "wellington", 5, "External", "Asset managers"),
    ("Invesco", "invesco", 1, "IVZ", "Asset managers"),
    ("Nuveen / TIAA", "tiaa", 1, "Search", "Asset managers"),
    ("PIMCO", "pimco", 1, "pimco-careers", "Asset managers"),
    ("PGIM", "pru", 5, "Careers", "Asset managers"),
    ("State Street", "statestreet", 1, "Global", "Asset managers"),
    ("Brown Brothers Harriman", "bbh", 5, "BBH", "Asset managers"),
]
WORKDAY_QUERIES = ["research", "portfolio", "investment", "equity", "analyst"]

GREENHOUSE = [
    # firm, board token, group
    ("Point72", "point72", "Hedge funds"),
    ("Schonfeld", "schonfeld", "Hedge funds"),
    ("ExodusPoint", "exoduspoint", "Hedge funds"),
    ("William Blair", "williamblair", "Banks"),
]

JPM_QUERIES = ["equity research", "research analyst", "asset management", "portfolio", "investment", "hedge fund"]

# ------------------------------------------------------------ directory
# Firms with a New York presence. "feed" firms are pulled automatically.
DIRECTORY = {
    "Banks": [
        ("Goldman Sachs", "https://higher.gs.com/"),
        ("JPMorgan Chase", "https://careers.jpmorgan.com/"),
        ("Morgan Stanley", "https://www.morganstanley.com/careers"),
        ("Citi", "https://jobs.citi.com/"),
        ("Bank of America", "https://careers.bankofamerica.com/"),
        ("Barclays", "https://search.jobs.barclays/"),
        ("Deutsche Bank", "https://careers.db.com/"),
        ("UBS", "https://www.ubs.com/global/en/careers.html"),
        ("Wells Fargo", "https://www.wellsfargojobs.com/"),
        ("Jefferies", "https://www.jefferies.com/careers/"),
        ("Evercore", "https://www.evercore.com/careers/"),
        ("Lazard", "https://www.lazard.com/careers/"),
        ("Moelis", "https://www.moelis.com/careers/"),
        ("PJT Partners", "https://pjtpartners.com/careers"),
        ("Centerview Partners", "https://www.centerviewpartners.com/"),
        ("Houlihan Lokey", "https://hl.com/careers/"),
        ("Guggenheim", "https://www.guggenheimpartners.com/careers"),
        ("Mizuho", "https://www.mizuhoamericas.com/careers"),
        ("Nomura", "https://www.nomura.com/americas/careers/"),
        ("RBC Capital Markets", "https://jobs.rbc.com/"),
        ("BNP Paribas", "https://group.bnpparibas/en/careers"),
        ("Piper Sandler", "https://www.pipersandler.com/careers"),
        ("William Blair", "https://www.williamblair.com/careers"),
        ("Wolfe Research", "https://www.wolferesearch.com/"),
    ],
    "Asset managers": [
        ("BlackRock", "https://careers.blackrock.com/"),
        ("Fidelity", "https://jobs.fidelity.com/"),
        ("Wellington", "https://www.wellington.com/en/careers"),
        ("AllianceBernstein", "https://www.alliancebernstein.com/corporate/en/careers.html"),
        ("Neuberger Berman", "https://www.nb.com/en/global/careers"),
        ("Invesco", "https://www.invesco.com/corporate/en/careers.html"),
        ("PIMCO", "https://pimco.wd1.myworkdayjobs.com/pimco-careers"),
        ("PGIM", "https://www.pgim.com/careers"),
        ("Nuveen", "https://www.nuveen.com/en-us/about-us/careers"),
        ("BNY", "https://www.bny.com/corporate/global/en/careers.html"),
        ("State Street", "https://careers.statestreet.com/"),
        ("Franklin Templeton", "https://www.franklintempletoncareers.com/"),
        ("Cohen & Steers", "https://www.cohenandsteers.com/careers/"),
        ("Lord Abbett", "https://www.lordabbett.com/en-us/financial-advisor/about-us/careers.html"),
        ("TCW", "https://www.tcw.com/careers"),
        ("Brown Brothers Harriman", "https://bbh.wd5.myworkdayjobs.com/BBH"),
        ("Jennison Associates", "https://www.jennison.com/"),
        ("Gabelli / GAMCO", "https://www.gabelli.com/"),
    ],
    "Hedge funds": [
        ("Citadel", "https://www.citadel.com/careers/"),
        ("Point72", "https://point72.com/"),
        ("Millennium", "https://www.mlp.com/careers/"),
        ("Balyasny", "https://www.bamfunds.com/careers"),
        ("D.E. Shaw", "https://www.deshaw.com/careers"),
        ("Schonfeld", "https://job-boards.greenhouse.io/schonfeld"),
        ("ExodusPoint", "https://job-boards.greenhouse.io/exoduspoint"),
        ("Marshall Wace", "https://www.mwam.com/"),
        ("Jain Global", "https://www.jainglobal.com/"),
        ("Tiger Global", "https://www.tigerglobal.com/"),
        ("Coatue", "https://www.coatue.com/"),
        ("Third Point", "https://www.thirdpoint.com/"),
        ("Elliott Management", "https://www.elliottmgmt.com/"),
        ("Davidson Kempner", "https://www.davidsonkempner.com/"),
        ("Hudson Bay Capital", "https://www.hudsonbaycapital.com/"),
        ("Verition", "https://www.verition.com/"),
        ("Sculptor", "https://www.sculptor.com/"),
        ("Walleye Capital", "https://www.walleyecapital.com/"),
        ("Brevan Howard", "https://www.brevanhoward.com/"),
        ("King Street", "https://www.kingstreet.com/"),
        ("Brigade Capital", "https://www.brigadecapital.com/"),
        ("Viking Global", "https://www.vikingglobal.com/"),
        ("Maverick Capital", "https://www.maverickcap.com/"),
        ("Pershing Square", "https://pershingsquareholdings.com/"),
        ("Starboard Value", "https://www.starboardvalue.com/"),
        ("Trian Partners", "https://trianpartners.com/"),
    ],
}

LINKEDIN_SEARCHES = [
    ("Equity research", "equity research analyst"),
    ("Investment analyst (buy side)", "investment analyst hedge fund"),
    ("Long/short equity", "long short equity analyst"),
    ("Asset management", "asset management investment associate"),
    ("Portfolio management", "portfolio manager associate"),
]

# ------------------------------------------------------------ filtering
NYC = re.compile(r"new york|\bny\b|\bnyc\b|manhattan", re.I)
EXCLUDE = re.compile(
    r"engineer|developer|software|technolog|\bit\b|infrastructure|cyber|security officer|compliance|"
    r"\blegal\b|counsel|paralegal|audit|\btax\b|accounting|accountant|payroll|recruit|talent acq|"
    r"human resources|\bhr\b|facilities|administrative|executive assistant|receptionist|"
    r"operations|ops\b|fund accounting|middle office|back office|controller|treasury ops|"
    r"marketing|communications|design|data center|help ?desk|service desk|call center|"
    r"teller|branch|mortgage|loan officer|relationship banker|financial advisor|wealth advisor|"
    r"client service|onboarding|kyc|aml|fraud|collections|underwrit|insurance|risk officer|credit risk|"
    r"product manager|account manager|trading assistant|evaluator|\bsales\b|banker|"
    r"quant|systematic|machine learning|\bml\b|\bnlp\b|data scien|statistical|microstructure|"
    r"private credit|private equity|private invest|private market|private client|private bank|wealth management|"
    r"real estate|real assets|secondaries|co-invest|direct lending|structured capital|structured finance|"
    r"structured products|structured equity|securitiz|asset[- ]backed|\babs\b|cmbs|rmbs|specialty finance|"
    r"alternative credit|venture|project finance|credit financing|lending|\bloans?\b|\bcib\b|"
    r"direct indexing|citigold|\buma\b|investor data|content associate|valuations|"
    r"cubist|\brisk\b|analytics|defined contribution|"
    r"intern\b.*summer.*2025",
    re.I,
)
CATEGORIES = [
    ("Credit research", re.compile(r"credit research|credit analyst|high yield|distressed|leveraged finance research", re.I)),
    ("Equity research", re.compile(r"equity research|research analyst|research associate|sector analyst|\bresearch\b.*equit|equit.*\bresearch\b", re.I)),
    ("Hedge fund / L-S", re.compile(r"long[/ -]short|hedge fund|fundamental equit|\bl/s\b|investment analyst|equity analyst|sector specialist|pod\b", re.I)),
    ("Portfolio management", re.compile(r"portfolio manag|\bpm\b|portfolio analyst|portfolio construction|investment team|investment professional", re.I)),
    ("Asset management", re.compile(r"asset management|investment management|investment associate|investment strategist|multi-asset|fund manag|investment specialist", re.I)),
]


def categorize(title: str) -> str | None:
    if EXCLUDE.search(title):
        return None
    for name, rx in CATEGORIES:
        if rx.search(title):
            return name
    return None


# ------------------------------------------------------------ http
def http_json(url: str, data: dict | None = None, retries: int = 2):
    body = json.dumps(data).encode() if data is not None else None
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=25, context=CTX) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            if attempt == retries:
                raise
            time.sleep(1.5 * (attempt + 1))


TODAY = dt.date.today()


def workday_date(posted_on: str | None) -> str | None:
    if not posted_on:
        return None
    s = posted_on.lower()
    if "today" in s:
        return TODAY.isoformat()
    if "yesterday" in s:
        return (TODAY - dt.timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\+?\s+days", s)
    if m:
        return (TODAY - dt.timedelta(days=int(m.group(1)))).isoformat()
    return None


# ------------------------------------------------------------ sources
def fetch_workday(firm, tenant, wd, site, group):
    base = f"https://{tenant}.wd{wd}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{tenant}/{site}/jobs"
    seen, out = set(), []
    for q in WORKDAY_QUERIES:
        for offset in range(0, 100, 20):
            d = http_json(api, {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": q})
            posts = d.get("jobPostings", [])
            for p in posts:
                path = p.get("externalPath")
                if not path or path in seen:
                    continue
                seen.add(path)
                loc = p.get("locationsText") or ""
                title = p.get("title", "")
                cat = categorize(title)
                if not cat or not NYC.search(loc):
                    continue
                out.append(dict(title=title, firm=firm, group=group, category=cat, location=loc,
                                posted=workday_date(p.get("postedOn")), url=f"{base}/en-US/{site}{path}"))
            if len(posts) < 20:
                break
            time.sleep(0.3)
    return out


def fetch_greenhouse(firm, token, group):
    d = http_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs")
    out = []
    for j in d.get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        title = j.get("title", "")
        cat = categorize(title)
        if not cat or not NYC.search(loc):
            continue
        out.append(dict(title=title, firm=firm, group=group, category=cat, location=loc,
                        posted=(j.get("updated_at") or "")[:10] or None, url=j.get("absolute_url")))
    return out


def fetch_jpm():
    base = "https://jpmc.fa.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
    seen, out = set(), []
    for q in JPM_QUERIES:
        for offset in range(0, 150, 25):
            finder = f'findReqs;siteNumber=CX_1001,keyword="{q}",limit=25,offset={offset},sortBy=POSTING_DATES_DESC'
            url = base + "?" + urllib.parse.urlencode({"onlyData": "true", "expand": "requisitionList.secondaryLocations", "finder": finder})
            items = http_json(url).get("items") or [{}]
            reqs = items[0].get("requisitionList", [])
            for j in reqs:
                jid = j.get("Id")
                if jid in seen:
                    continue
                seen.add(jid)
                loc = j.get("PrimaryLocation", "")
                title = j.get("Title", "")
                cat = categorize(title)
                if not cat or not NYC.search(loc):
                    continue
                out.append(dict(title=title, firm="JPMorgan Chase", group="Banks", category=cat, location=loc,
                                posted=j.get("PostedDate"),
                                url=f"https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/{jid}"))
            if len(reqs) < 25:
                break
            time.sleep(0.3)
    return out


def collect():
    tasks = {}
    with cf.ThreadPoolExecutor(8) as ex:
        for f, t, wd, s, g in WORKDAY:
            tasks[ex.submit(fetch_workday, f, t, wd, s, g)] = f
        for f, t, g in GREENHOUSE:
            tasks[ex.submit(fetch_greenhouse, f, t, g)] = f
        tasks[ex.submit(fetch_jpm)] = "JPMorgan Chase"
        jobs, ok, failed = [], [], []
        for fut in cf.as_completed(tasks):
            firm = tasks[fut]
            try:
                jobs.extend(fut.result())
                ok.append(firm)
            except Exception as e:  # noqa: BLE001
                failed.append(firm)
                print(f"  ! {firm}: {type(e).__name__}: {e}", file=sys.stderr)
    # de-duplicate by url
    uniq = {j["url"]: j for j in jobs}
    jobs = sorted(uniq.values(), key=lambda j: (j["posted"] or "0000", j["firm"]), reverse=True)
    return jobs, sorted(ok), sorted(failed)


def load_linkedin():
    if not LINKEDIN.exists():
        return [], None
    try:
        data = json.loads(LINKEDIN.read_text())
    except json.JSONDecodeError:
        return [], None
    rows = data.get("jobs", data) if isinstance(data, dict) else data
    saved = data.get("saved_at") if isinstance(data, dict) else None
    clean = []
    for r in rows if isinstance(rows, list) else []:
        url = str(r.get("url", ""))
        if not url.startswith("https://www.linkedin.com/"):
            continue
        clean.append({k: str(r.get(k, "") or "") for k in ("title", "firm", "location", "posted")} | {"url": url})
    return clean, saved


# ------------------------------------------------------------ render
def e(s) -> str:
    return html.escape(str(s or ""), quote=True)


def render(jobs, ok, failed, li_jobs, li_saved):
    feed_firms = set(ok)
    updated = dt.datetime.now(dt.timezone.utc).astimezone().strftime("%b %d, %Y")
    week_ago = (TODAY - dt.timedelta(days=7)).isoformat()
    firms_with_roles = sorted({j["firm"] for j in jobs})
    cats = [c for c, _ in CATEGORIES if any(j["category"] == c for j in jobs)]

    rows = []
    for j in jobs:
        new = j["posted"] and j["posted"] >= week_ago
        posted = dt.date.fromisoformat(j["posted"]).strftime("%b %d") if j["posted"] else "—"
        rows.append(
            f'<tr data-firm="{e(j["firm"])}" data-cat="{e(j["category"])}" data-new="{1 if new else 0}">'
            f'<td><a href="{e(j["url"])}" target="_blank" rel="noopener">{e(j["title"])}</a>'
            f'{" <span class=\"new\">New</span>" if new else ""}</td>'
            f'<td>{e(j["firm"])}</td><td><span class="tag">{e(j["category"])}</span></td>'
            f'<td class="loc">{e(j["location"])}</td><td class="num">{e(posted)}</td></tr>'
        )
    table = "\n".join(rows) or '<tr><td colspan="5">No matching roles found today.</td></tr>'

    li_links = "\n".join(
        f'<li><a href="https://www.linkedin.com/jobs/search/?{urllib.parse.urlencode({"keywords": q, "location": "New York, New York, United States", "f_TPR": "r604800"})}" '
        f'target="_blank" rel="noopener">{e(label)}</a> <span class="muted">· New York · past week</span></li>'
        for label, q in LINKEDIN_SEARCHES
    )
    if li_jobs:
        li_rows = "\n".join(
            f'<tr><td><a href="{e(r["url"])}" target="_blank" rel="noopener">{e(r["title"])}</a></td>'
            f'<td>{e(r["firm"])}</td><td class="loc">{e(r["location"])}</td><td class="num">{e(r["posted"])}</td></tr>'
            for r in li_jobs
        )
        li_saved_txt = f"Saved {e(li_saved)}. " if li_saved else ""
        li_block = (f'<p class="note">{li_saved_txt}Collected from LinkedIn in a supervised browser session, not scraped automatically.</p>'
                    f'<div class="table-wrap"><table><thead><tr><th>Role</th><th>Firm</th><th>Location</th><th>Posted</th></tr></thead>'
                    f'<tbody>{li_rows}</tbody></table></div>')
    else:
        li_block = '<p class="note">No saved LinkedIn results yet. Use the searches above to open live results on LinkedIn.</p>'

    dir_html = []
    for group, firms in DIRECTORY.items():
        items = "\n".join(
            f'<li><a href="{e(u)}" target="_blank" rel="noopener">{e(n)}</a>'
            f'{" <span class=\"live\" title=\"Roles pulled into the list above\">live</span>" if n in feed_firms or (n == "JPMorgan Chase" and "JPMorgan Chase" in feed_firms) else ""}</li>'
            for n, u in firms
        )
        dir_html.append(f'<div class="dir-col"><h3>{e(group)}</h3><ul>{items}</ul></div>')

    firm_opts = "\n".join(f'<option value="{e(f)}">{e(f)}</option>' for f in firms_with_roles)
    cat_opts = "\n".join(f'<option value="{e(c)}">{e(c)}</option>' for c in cats)
    failed_note = (f' Couldn\'t reach {e(", ".join(failed))} on the last update.' if failed else "")

    return TEMPLATE.format(
        updated=e(updated), n_jobs=len(jobs), n_firms=len(firms_with_roles), n_feeds=len(ok),
        table=table, firm_opts=firm_opts, cat_opts=cat_opts, li_links=li_links, li_block=li_block,
        directory="\n".join(dir_html), failed_note=failed_note,
    )


TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>NYC Buy-Side Jobs — Bennett Lee</title>
  <meta name="description" content="Open investment management, equity research and hedge fund roles in New York, pulled from firms' career sites.">
  <style>
    :root {{
      --columbia: #B9D9EB; --columbia-soft: #E6F1F8; --navy: #1D4F91; --deep: #0C2340;
      --bg: #F7FAFC; --card: #FFFFFF; --text: #1A2B3C; --muted: #55697D;
      --radius: 14px; --shadow: 0 2px 10px rgba(12, 35, 64, 0.07);
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      background: var(--bg); color: var(--text); line-height: 1.6; }}
    a {{ color: var(--navy); }}
    a:focus-visible, button:focus-visible, select:focus-visible, input:focus-visible {{ outline: 3px solid var(--navy); outline-offset: 2px; }}
    nav {{ position: sticky; top: 0; z-index: 10; background: var(--deep); border-bottom: 3px solid var(--columbia); }}
    .nav-inner {{ max-width: 1040px; margin: 0 auto; padding: 0.85rem 1.5rem; display: flex; align-items: center;
      justify-content: space-between; gap: 1rem; flex-wrap: wrap; }}
    .brand {{ color: #fff; font-weight: 700; text-decoration: none; letter-spacing: 0.02em; }}
    .brand span {{ color: var(--columbia); }}
    .nav-links {{ display: flex; gap: 1.4rem; list-style: none; flex-wrap: wrap; }}
    .nav-links a {{ color: #DCE9F3; text-decoration: none; font-size: 0.95rem; font-weight: 500;
      padding-bottom: 2px; border-bottom: 2px solid transparent; }}
    .nav-links a:hover, .nav-links a.active {{ color: #fff; border-bottom-color: var(--columbia); }}
    .nav-links a.nav-cta {{ background: var(--columbia); color: var(--deep); font-weight: 700;
      padding: 0.25rem 0.8rem; border-radius: 999px; border-bottom: none; }}
    .nav-links a.nav-cta:hover {{ background: #fff; }}
    header.hero {{ background: linear-gradient(135deg, var(--columbia) 0%, var(--columbia-soft) 100%); padding: 3rem 1.5rem 2.5rem; }}
    .hero-inner {{ max-width: 1040px; margin: 0 auto; }}
    .hero h1 {{ color: var(--deep); font-size: clamp(2rem, 5vw, 2.8rem); line-height: 1.15; }}
    .hero p {{ max-width: 680px; color: #23384D; margin-top: 0.6rem; }}
    .stats {{ display: flex; flex-wrap: wrap; gap: 0.6rem; margin-top: 1.1rem; }}
    .stat {{ background: var(--card); border-radius: 999px; padding: 0.3rem 0.9rem; font-size: 0.9rem; box-shadow: var(--shadow); }}
    .stat b {{ color: var(--navy); }}
    main {{ max-width: 1040px; margin: 0 auto; padding: 1rem 1.5rem 3rem; }}
    section {{ padding-top: 2.5rem; }}
    .section-head {{ display: flex; align-items: baseline; gap: 0.75rem; margin-bottom: 1rem; }}
    .section-head h2 {{ font-size: 1.5rem; color: var(--deep); }}
    .section-head .bar {{ flex: 1; height: 2px; background: var(--columbia); }}
    .card {{ background: var(--card); border-radius: var(--radius); box-shadow: var(--shadow); padding: 1.2rem 1.3rem; }}
    .filters {{ display: flex; flex-wrap: wrap; gap: 0.7rem; align-items: center; margin-bottom: 1rem; }}
    .filters input[type=search], .filters select {{ font: inherit; padding: 0.45rem 0.7rem; border: 1px solid var(--columbia);
      border-radius: 8px; background: #fff; color: var(--text); min-width: 0; }}
    .filters input[type=search] {{ flex: 1 1 220px; }}
    .filters label.check {{ display: inline-flex; gap: 0.4rem; align-items: center; font-size: 0.95rem; }}
    .count {{ color: var(--muted); font-size: 0.9rem; margin-left: auto; }}
    .table-wrap {{ overflow-x: auto; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.95rem; }}
    th {{ text-align: left; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted);
      padding: 0.5rem 0.6rem; border-bottom: 2px solid var(--columbia); white-space: nowrap; }}
    td {{ padding: 0.6rem; border-bottom: 1px solid #E3EDF5; vertical-align: top; }}
    td a {{ font-weight: 600; text-decoration: none; }}
    td a:hover {{ text-decoration: underline; }}
    td.loc {{ color: var(--muted); font-size: 0.88rem; min-width: 140px; }}
    td.num {{ white-space: nowrap; font-variant-numeric: tabular-nums; color: var(--muted); }}
    .tag {{ background: var(--columbia-soft); border: 1px solid var(--columbia); color: var(--deep); border-radius: 999px;
      padding: 0.1rem 0.55rem; font-size: 0.8rem; white-space: nowrap; }}
    .new {{ background: var(--navy); color: #fff; border-radius: 999px; padding: 0.05rem 0.45rem; font-size: 0.72rem;
      font-weight: 700; margin-left: 0.3rem; vertical-align: middle; }}
    .note, .muted {{ color: var(--muted); font-size: 0.92rem; }}
    .note {{ margin-bottom: 0.8rem; }}
    ul.plain {{ list-style: none; display: grid; gap: 0.4rem; }}
    .dir {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem; }}
    .dir-col h3 {{ font-size: 1rem; color: var(--deep); margin-bottom: 0.4rem; }}
    .dir-col ul {{ list-style: none; display: grid; gap: 0.25rem; font-size: 0.93rem; }}
    .dir-col a {{ text-decoration: none; }}
    .dir-col a:hover {{ text-decoration: underline; }}
    .live {{ font-size: 0.7rem; font-weight: 700; color: var(--navy); background: var(--columbia-soft);
      border: 1px solid var(--columbia); border-radius: 999px; padding: 0 0.4rem; margin-left: 0.25rem; }}
    footer {{ background: var(--deep); color: #C9DAE8; text-align: center; padding: 2rem 1.5rem; font-size: 0.9rem; }}
    footer a {{ color: var(--columbia); }}
    @media (max-width: 860px) {{ .dir {{ grid-template-columns: 1fr 1fr; }} }}
    @media (max-width: 560px) {{ .dir {{ grid-template-columns: 1fr; }} .count {{ margin-left: 0; }} }}
  </style>
</head>
<body>
  <nav aria-label="Main">
    <div class="nav-inner">
      <a class="brand" href="index.html">Bennett <span>Lee</span></a>
      <ul class="nav-links">
        <li><a href="index.html#education">Education</a></li>
        <li><a href="index.html#experience">Experience</a></li>
        <li><a href="index.html#additional">Additional</a></li>
        <li><a href="research.html">Research</a></li>
        <li><a href="dashboard.html">Dashboard</a></li>
        <li><a href="movers.html">Movers</a></li>
        <li><a class="active" href="jobs.html" aria-current="page">Jobs</a></li>
        <li><a href="index.html#contact">Contact</a></li>
        <li><a class="nav-cta" href="Lee_Bennett_Resume_2026.pdf" target="_blank" rel="noopener">Resume ↓</a></li>
      </ul>
    </div>
  </nav>

  <header class="hero">
    <div class="hero-inner">
      <h1>NYC Buy-Side &amp; Research Jobs</h1>
      <p>Open investment management, equity research and hedge fund roles in New York, pulled from firms' own career sites. Updated {updated}.</p>
      <div class="stats">
        <span class="stat"><b>{n_jobs}</b> open roles</span>
        <span class="stat"><b>{n_firms}</b> firms hiring</span>
        <span class="stat"><b>{n_feeds}</b> career sites checked</span>
      </div>
    </div>
  </header>

  <main>
    <section id="roles" aria-labelledby="roles-h">
      <div class="section-head"><h2 id="roles-h">Open roles</h2><span class="bar"></span></div>
      <div class="card">
        <div class="filters">
          <label for="q" class="sr-only" hidden>Search</label>
          <input id="q" type="search" placeholder="Search titles, e.g. healthcare, credit, associate" aria-label="Search role titles">
          <select id="cat" aria-label="Role type"><option value="">All role types</option>{cat_opts}</select>
          <select id="firm" aria-label="Firm"><option value="">All firms</option>{firm_opts}</select>
          <label class="check"><input id="new" type="checkbox"> Posted this week</label>
          <span class="count" id="count" aria-live="polite"></span>
        </div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Role</th><th>Firm</th><th>Type</th><th>Location</th><th>Posted</th></tr></thead>
            <tbody id="rows">
{table}
            </tbody>
          </table>
        </div>
      </div>
      <p class="note" style="margin-top:0.8rem">Roles are matched on job title, so a few may be off-target. Dates are when the firm posted or last updated the role.{failed_note}</p>
    </section>

    <section id="linkedin" aria-labelledby="li-h">
      <div class="section-head"><h2 id="li-h">LinkedIn</h2><span class="bar"></span></div>
      <div class="card">
        <p class="note">LinkedIn doesn't allow automated collection, so these open live searches on LinkedIn instead.</p>
        <ul class="plain">{li_links}</ul>
        <div style="margin-top:1.2rem">{li_block}</div>
      </div>
    </section>

    <section id="firms" aria-labelledby="firms-h">
      <div class="section-head"><h2 id="firms-h">Firm directory</h2><span class="bar"></span></div>
      <p class="note">Career pages for firms with a New York presence. Firms marked <span class="live">live</span> are checked automatically for the list above. The rest, including Goldman Sachs, Citadel and D.E. Shaw, don't offer a public feed, so check them directly.</p>
      <div class="card dir">
{directory}
      </div>
    </section>
  </main>

  <footer>
    <p>Pulled from public career sites. Always confirm details on the firm's own posting. · <a href="index.html">Back to home</a></p>
  </footer>

  <script>
    (function () {{
      var q = document.getElementById('q'), cat = document.getElementById('cat'),
          firm = document.getElementById('firm'), nw = document.getElementById('new'),
          count = document.getElementById('count'),
          rows = Array.prototype.slice.call(document.querySelectorAll('#rows tr[data-firm]'));
      function apply() {{
        var t = q.value.trim().toLowerCase(), shown = 0;
        rows.forEach(function (r) {{
          var ok = (!t || r.textContent.toLowerCase().indexOf(t) !== -1) &&
                   (!cat.value || r.dataset.cat === cat.value) &&
                   (!firm.value || r.dataset.firm === firm.value) &&
                   (!nw.checked || r.dataset.new === '1');
          r.hidden = !ok; if (ok) shown++;
        }});
        count.textContent = shown + ' of ' + rows.length + ' roles';
      }}
      [q, cat, firm, nw].forEach(function (el) {{ el.addEventListener('input', apply); el.addEventListener('change', apply); }});
      apply();
    }})();
  </script>
</body>
</html>
"""


def main():
    print("Fetching roles...", file=sys.stderr)
    jobs, ok, failed = collect()
    li_jobs, li_saved = load_linkedin()
    OUT.write_text(render(jobs, ok, failed, li_jobs, li_saved), encoding="utf-8")
    print(f"Wrote {OUT.name}: {len(jobs)} roles from {len(ok)} sources ({len(failed)} failed).", file=sys.stderr)


if __name__ == "__main__":
    main()
