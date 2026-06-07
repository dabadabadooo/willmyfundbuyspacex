#!/usr/bin/env python3
"""
fetch_edgar.py
--------------
Pulls public SpaceX-related data from SEC EDGAR and writes JSON files that the
static site reads. Designed to run on a schedule (see .github/workflows).

Three datasets are produced in ../data/:
  1. issuer_filings.json   - SpaceX's own filings (S-1, S-1/A, 424B, FWP, DRS...)
  2. fund_holders.json     - Registered funds disclosing a Space Exploration
                             Technologies position in NPORT-P, ranked by $ value.
  3. institutional_13f.json- Institutions reporting SPCX in 13F-HR. Empty until
                             the first 13Fs covering the IPO quarter post
                             (~mid-Aug 2026); the query is wired up and ready.
  + meta.json              - IPO facts, counts, timestamps, sources, disclaimer.

SEC fair-access policy requires a descriptive User-Agent with real contact
info and limits clients to ~10 requests/sec. Set SEC_USER_AGENT before running.
"""

import json
import os
import re
import sys
import time
import datetime as dt
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import quote as _urlquote

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
SPACEX_CIK = 1181412                       # Space Exploration Technologies Corp
SPACEX_TICKER = "SPCX"
EXCHANGE = "Nasdaq"
OFFERING_FILE_NUMBER = "333-296070"
# Funds report the holding under different names: Fidelity / Baron / ARK Venture
# write "Space Exploration Technologies Corp"; T. Rowe Price, Blackstone, and
# several interval funds write "SpaceX". Query both aliases and merge results.
ISSUER_NAME_QUERIES = ["Space Exploration Technologies", "SpaceX"]

# Expected IPO facts. These are NOT in EDGAR structured data pre-pricing; they
# come from the prospectus / press reporting and are shown as "expected" on the
# site. Update after the final 424B prices. (Sources tracked in meta.sources.)
IPO_FACTS = {
    "status": "Pre-IPO (roadshow underway)",
    "expected_pricing_date": "2026-06-11",
    "expected_first_trade_date": "2026-06-12",
    "expected_price_usd": 135,
    "expected_shares_offered": 556_600_000,
    "expected_raise_usd": 75_000_000_000,
    "expected_valuation_usd": 1_750_000_000_000,
    "ticker": SPACEX_TICKER,
    "exchange": EXCHANGE,
    "file_number": OFFERING_FILE_NUMBER,
    "cik": SPACEX_CIK,
}

SOURCES = [
    {"label": "SEC EDGAR — SpaceX filings (CIK 1181412)",
     "url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={SPACEX_CIK}&type=&dateb=&owner=include&count=40"},
    {"label": "SEC EDGAR full-text search — NPORT-P fund holdings",
     "url": "https://efts.sec.gov/LATEST/search-index?q=%22Space+Exploration+Technologies%22&forms=NPORT-P"},
    {"label": "Expected IPO terms as reported (Reuters / Bloomberg / CNBC, Jun 2026)",
     "url": "https://www.reuters.com/"},
]

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "site", "data")

# How many NPORT-P full-text hits to scan, and how many filings to actually
# fetch+parse. Recent quarters first; raise these for deeper history.
MAX_HITS = int(os.environ.get("MAX_HITS", "1500"))
MAX_FETCH = int(os.environ.get("MAX_FETCH", "500"))

USER_AGENT = os.environ.get(
    "SEC_USER_AGENT",
    "spacex-ipo-tracker (set SEC_USER_AGENT env var to name + email)"
)
# Optional Cloudflare Worker proxy URL. When set, all SEC EDGAR requests are
# routed through the Worker (which runs on Cloudflare IPs that SEC doesn't
# block) instead of going directly from GitHub Actions (Azure IPs are blocked).
# Set repo variable EDGAR_PROXY = https://<your-worker>.workers.dev
EDGAR_PROXY = os.environ.get("EDGAR_PROXY", "").rstrip("/")
RATE_DELAY = 0.15  # seconds between SEC requests (~7/sec, under the 10/sec cap)


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #
def _get(url, expect_json=False, retries=3):
    """GET with the required UA header, basic retry/backoff, polite delay."""
    # Route through the Cloudflare Worker proxy when configured, so requests
    # arrive at SEC EDGAR from Cloudflare IPs instead of GitHub Actions (Azure).
    fetch_url = f"{EDGAR_PROXY}?url={_urlquote(url, safe='')}" if EDGAR_PROXY else url
    last = None
    for attempt in range(retries):
        try:
            req = Request(fetch_url, headers={
                "User-Agent": USER_AGENT,
                "Accept-Encoding": "gzip, deflate",
                "Accept": "application/json, text/html, */*",
            })
            with urlopen(req, timeout=30) as r:
                raw = r.read()
                enc = r.headers.get("Content-Encoding", "")
                if "gzip" in enc:
                    import gzip
                    raw = gzip.decompress(raw)
                text = raw.decode("utf-8", errors="ignore")
                time.sleep(RATE_DELAY)
                return json.loads(text) if expect_json else text
        except (HTTPError, URLError) as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    print(f"  ! request failed: {url} ({last})", file=sys.stderr)
    return None


def _archive_url(cik, accession, filename):
    accnodash = accession.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accnodash}/{filename}"


def _index_url(cik, accession):
    accnodash = accession.replace("-", "")
    return (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accnodash}/"
            f"{accession}-index.htm")


def _tag(block, tag):
    m = re.search(rf"<{tag}>(.*?)</{tag}>", block, re.S)
    return m.group(1).strip() if m else None


# --------------------------------------------------------------------------- #
# 1. SpaceX issuer filings
# --------------------------------------------------------------------------- #
KEEP_FORMS = ("S-1", "S-1/A", "424B", "FWP", "DRS", "DRS/A", "8-A", "8-K", "424A")

FORM_LABELS = {
    "S-1": "Registration statement (IPO prospectus)",
    "S-1/A": "Amended registration statement",
    "424B1": "Final prospectus", "424B2": "Final prospectus",
    "424B3": "Final prospectus", "424B4": "Final prospectus (priced)",
    "FWP": "Free writing prospectus",
    "DRS": "Draft registration statement (confidential)",
    "DRS/A": "Amended draft registration statement",
    "8-A12B": "Securities registration (exchange listing)",
}


def fetch_issuer_filings():
    print("[1/3] SpaceX issuer filings...")
    data = _get(f"https://data.sec.gov/submissions/CIK{SPACEX_CIK:010d}.json",
                expect_json=True)
    out = []
    if not data:
        return out
    r = data["filings"]["recent"]
    n = len(r["form"])
    for i in range(n):
        form = r["form"][i]
        if not any(form.startswith(f) for f in KEEP_FORMS):
            continue
        acc = r["accessionNumber"][i]
        out.append({
            "form": form,
            "label": FORM_LABELS.get(form, FORM_LABELS.get(form.split("/")[0], "")),
            "filed": r["filingDate"][i],
            "accession": acc,
            "primary_doc": _archive_url(SPACEX_CIK, acc, r["primaryDocument"][i]),
            "filing_index": _index_url(SPACEX_CIK, acc),
        })
    out.sort(key=lambda x: x["filed"], reverse=True)
    print(f"      {len(out)} filings")
    return out


# --------------------------------------------------------------------------- #
# 2. Fund holders (NPORT-P)
# --------------------------------------------------------------------------- #
def _fts(query, form, frm=0):
    url = (f"https://efts.sec.gov/LATEST/search-index?q=%22"
           f"{query.replace(' ', '+')}%22&forms={form}&from={frm}")
    return _get(url, expect_json=True)


def _collect_hits(form):
    """Full-text search across all issuer name aliases; dedupe by filing id."""
    seen, hits = set(), []
    for query in ISSUER_NAME_QUERIES:
        first = _fts(query, form, 0)
        if not first:
            continue
        total = first.get("hits", {}).get("total", {}).get("value", 0)
        pages = min(total, MAX_HITS)
        frm = 0
        while frm < pages:
            page = first if frm == 0 else _fts(query, form, frm)
            if not page:
                break
            batch = page.get("hits", {}).get("hits", [])
            if not batch:
                break
            for h in batch:
                hid = h.get("_id", "")
                if hid in seen:
                    continue
                seen.add(hid)
                s = h.get("_source", {})
                ciks = s.get("ciks") or [None]
                hits.append({
                    "id": hid,
                    "cik": ciks[0],
                    "registrant": (s.get("display_names") or [""])[0],
                    "file_date": s.get("file_date"),
                })
            frm += len(batch)
    return hits


def _parse_nport(cik, accession):
    """Fetch raw NPORT XML, return (series, period, total_val, total_pct)."""
    url = _archive_url(cik, accession, "primary_doc.xml")
    xml = _get(url)
    if not xml:
        return None
    series = _tag(xml, "seriesName")
    series_id = _tag(xml, "seriesId")
    period = _tag(xml, "repPdDate") or _tag(xml, "repPdEnd")
    total_val, total_pct, items = 0.0, 0.0, 0
    for m in re.finditer(r"<invstOrSec>.*?</invstOrSec>", xml, re.S):
        b = m.group(0)
        name = _tag(b, "name") or ""
        # Filers vary in casing: "Space Exploration Technologies" (Baron) vs
        # "SPACE EXPLORATION TECHNOLOGIES CORP" (Fidelity). Match the holding
        # NAME, case-insensitively, and require the full phrase to avoid
        # matching other space-sector names (Relativity Space, etc.).
        if not re.search(r"space\s*exploration\s*technolog|spacex", name, re.I):
            continue
        v = _tag(b, "valUSD"); p = _tag(b, "pctVal")
        try:
            total_val += float(v) if v else 0.0
        except ValueError:
            pass
        try:
            total_pct += float(p) if p else 0.0
        except ValueError:
            pass
        items += 1
    if items == 0:
        return None
    return {"series": series, "series_id": series_id, "period": period,
            "value_usd": round(total_val, 2), "pct_of_fund": round(total_pct, 4)}


def fetch_fund_holders():
    print("[2/3] Fund holders (NPORT-P full-text search)...")
    hits = _collect_hits("NPORT-P")
    hits = [h for h in hits if h["cik"] and h["file_date"]]
    print(f"      {len(hits)} hits across "
          f"{len(set(h['cik'] for h in hits))} registrants; "
          f"parsing up to {MAX_FETCH}...")

    # Group accessions by registrant (newest first), then round-robin across
    # registrants so a prolific filer (Baron) doesn't starve others (Fidelity).
    by_cik = {}
    for h in sorted(hits, key=lambda x: x["file_date"], reverse=True):
        acc = h["id"].split(":")[0]
        bucket = by_cik.setdefault(h["cik"], {"reg": h["registrant"], "accs": []})
        if (acc, h["file_date"]) not in [(a, d) for a, d in bucket["accs"]]:
            bucket["accs"].append((acc, h["file_date"]))

    order, ptrs, active = [], {c: 0 for c in by_cik}, list(by_cik)
    while active and len(order) < MAX_FETCH:
        for c in list(active):
            i = ptrs[c]
            accs = by_cik[c]["accs"]
            if i >= len(accs):
                active.remove(c)
                continue
            order.append((c, by_cik[c]["reg"], accs[i][0], accs[i][1]))
            ptrs[c] += 1
            if len(order) >= MAX_FETCH:
                break

    seen, holders = {}, []
    for cik, registrant, acc, fdate in order:
        parsed = _parse_nport(cik, acc)
        if not parsed:
            continue
        key = (cik, parsed["series"])
        if key in seen and seen[key] >= (parsed["period"] or ""):
            continue
        seen[key] = parsed["period"] or ""
        rec = {
            "registrant": re.sub(r"\s*\(CIK.*\)\s*", "", registrant).strip(),
            "fund": parsed["series"],
            "series_id": parsed.get("series_id"),
            "value_usd": parsed["value_usd"],
            "pct_of_fund": parsed["pct_of_fund"],
            "period": parsed["period"],
            "form": "NPORT-P",
            "filed": fdate,
            "filing_index": _index_url(cik, acc),
        }
        holders = [x for x in holders
                   if not (x["registrant"] == rec["registrant"]
                           and x["fund"] == rec["fund"])]
        holders.append(rec)

    holders.sort(key=lambda x: x["value_usd"], reverse=True)

    # Attach the publicly traded ticker(s) for each fund so people can match
    # them against their 401(k)/IRA menu. SEC maps series -> class -> symbol.
    tmap = _series_ticker_map()
    for h in holders:
        h["tickers"] = tmap.get(h.get("series_id"), [])

    print(f"      {len(holders)} unique fund positions")
    return holders


def _series_ticker_map():
    """series_id -> [tickers] from SEC's investment-company ticker dataset."""
    data = _get("https://www.sec.gov/files/company_tickers_mf.json",
                expect_json=True)
    m = {}
    if not data:
        return m
    for row in data.get("data", []):  # fields: [cik, seriesId, classId, symbol]
        try:
            sid, sym = row[1], row[3]
        except (IndexError, TypeError):
            continue
        if not sid or not sym:
            continue
        m.setdefault(sid, [])
        if sym not in m[sid]:
            m[sid].append(sym)
    return m


# --------------------------------------------------------------------------- #
# 3. Institutional 13F holders (populates post-IPO)
# --------------------------------------------------------------------------- #
def fetch_13f():
    print("[3/3] Institutional 13F-HR holders (SPCX)...")
    hits = _collect_hits("13F-HR")
    out = []
    seen = set()
    for h in hits[:50]:
        key = (h["cik"], h["file_date"])
        if key in seen:
            continue
        seen.add(key)
        acc = h["id"].split(":")[0]
        out.append({
            "manager": re.sub(r"\s*\(CIK.*\)\s*", "", h["registrant"]).strip(),
            "form": "13F-HR",
            "filed": h["file_date"],
            "filing_index": _index_url(h["cik"], acc),
        })
    print(f"      {len(out)} 13F filings mentioning the issuer")
    return out


# --------------------------------------------------------------------------- #
# 4. Principal stockholders (S-1 beneficial ownership table)
# --------------------------------------------------------------------------- #
def _parse_ownership(html):
    """Parse the 'Security Ownership of Certain Beneficial Owners' table."""
    t = re.sub(r"<[^>]+>", " ", html)
    for a, b in [("&#160;", " "), ("&nbsp;", " "), ("&amp;", "&"),
                 ("&#8212;", "-"), ("&#8217;", "'"), ("&#8220;", '"'),
                 ("&#8221;", '"'), ("&#58;", ":"), ("&#59;", ";"),
                 ("&#8226;", "*"), ("&#47;", "/")]:
        t = t.replace(a, b)
    t = re.sub(r"\s+", " ", t)
    sec = t.find("SECURITY OWNERSHIP OF CERTAIN BENEFICIAL OWNERS")
    if sec < 0:
        return None
    end = t.find("Represents beneficial ownership", sec)
    win = t[sec: end + 60 if end > sec else sec + 8000]

    m_as = re.search(r"ownership of our common stock as of "
                     r"([A-Z][a-z]+ \d{1,2}, \d{4})", win)
    as_of = m_as.group(1) if m_as else None
    price = IPO_FACTS.get("expected_price_usd")

    NUM = r"(?:[\d,]+|-)"
    PCT = r"(?:[\d.]+\s*%|\*)"
    rowre = re.compile(
        r"([A-Z][A-Za-z.'\-]+(?:\s+[A-Za-z.'\-]+)*?)\s*(?:\((\d)\))?\s*\.{3,}\s*"
        + f"({NUM})\\s+({PCT})\\s+({NUM})\\s+({PCT})\\s+({PCT})\\s+"
        + f"({NUM})\\s+({PCT})\\s+({NUM})\\s+({PCT})\\s+({PCT})")

    def num(x):
        x = x.replace(",", "").strip()
        return 0 if x in ("-", "") else int(x)

    def pct(x):
        x = x.strip()
        return None if x == "*" else float(x.replace("%", "").strip())

    holders, seen = [], set()
    for m in rowre.finditer(win):
        name = m.group(1).strip()
        if name.lower().startswith(("class", "number", "shares", "combined",
                                    "named", "percentage", "table")):
            continue
        if name in seen:
            continue
        seen.add(name)
        a_sh, b_sh = num(m.group(3)), num(m.group(5))
        tot = a_sh + b_sh
        holders.append({
            "name": name,
            "footnote": m.group(2),
            "class_a_shares": a_sh, "class_a_pct": pct(m.group(4)),
            "class_b_shares": b_sh, "class_b_pct": pct(m.group(6)),
            "combined_voting_pct": pct(m.group(7)),
            "total_shares": tot,
            "implied_value_usd": tot * price if price else None,
        })

    group = None
    g = re.search(r"All executive officers and directors as a group "
                  r"\((\d+) persons\)\s*\.+\s*"
                  + f"({NUM})\\s+({PCT})\\s+({NUM})\\s+({PCT})\\s+({PCT})", win)
    if g:
        a_sh, b_sh = num(g.group(2)), num(g.group(4))
        tot = a_sh + b_sh
        group = {"persons": int(g.group(1)),
                 "class_a_shares": a_sh, "class_a_pct": pct(g.group(3)),
                 "class_b_shares": b_sh, "class_b_pct": pct(g.group(5)),
                 "combined_voting_pct": pct(g.group(6)),
                 "total_shares": tot,
                 "implied_value_usd": tot * price if price else None}

    holders.sort(key=lambda x: x["total_shares"], reverse=True)
    return {"holders": holders, "group": group, "as_of": as_of, "source": None}


def fetch_principal_holders(issuer):
    print("[4/4] Principal stockholders (S-1 beneficial ownership)...")
    cand = [f for f in issuer if f["form"].startswith(("S-1", "424B"))]
    cand.sort(key=lambda x: x["filed"], reverse=True)
    for f in cand:
        html = _get(f["primary_doc"])
        if not html:
            continue
        parsed = _parse_ownership(html)
        if parsed and parsed["holders"]:
            parsed["source"] = {"form": f["form"], "filed": f["filed"],
                                "url": f["filing_index"]}
            parsed["price_basis_usd"] = IPO_FACTS.get("expected_price_usd")
            print(f"      {len(parsed['holders'])} named holders "
                  f"from {f['form']} filed {f['filed']}")
            return parsed
    print("      no ownership table parsed")
    return {"holders": [], "group": None, "as_of": None, "source": None}


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    if "set SEC_USER_AGENT" in USER_AGENT:
        print("WARNING: SEC_USER_AGENT not set. SEC requires a real contact "
              "(e.g. 'Project Name you@yourdomain.com'). Proceeding, but you "
              "may be rate-limited or blocked.\n", file=sys.stderr)

    os.makedirs(OUT_DIR, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()

    issuer = fetch_issuer_filings()
    holders = fetch_fund_holders()
    f13 = fetch_13f()
    principal = fetch_principal_holders(issuer)

    # Safety guard: if SEC blocked all requests, abort rather than
    # overwrite good existing data files with empty ones.
    if not issuer and not holders:
        print("\nERROR: All SEC requests failed (likely IP block or missing "
              "SEC_USER_AGENT). Keeping existing data files unchanged.",
              file=sys.stderr)
        sys.exit(1)

    total_exposure = round(sum(h["value_usd"] for h in holders), 2)
    top_voting = (principal["group"]["combined_voting_pct"]
                  if principal.get("group") else None)

    sources = list(SOURCES)
    if principal.get("source"):
        sources.append({
            "label": "SEC EDGAR — SpaceX S-1 beneficial ownership table",
            "url": principal["source"]["url"]})
    sources.append({
        "label": "U.S. DOL EBSA — Form 5500 datasets (employer & government plans)",
        "url": "https://www.dol.gov/agencies/ebsa/about-ebsa/our-activities/"
               "public-disclosure/foia/form-5500-datasets"})

    meta = {
        "project": "SpaceX IPO — Fund Exposure Tracker",
        "last_updated_utc": now,
        "ipo": IPO_FACTS,
        "counts": {
            "issuer_filings": len(issuer),
            "fund_positions": len(holders),
            "institutional_13f": len(f13),
            "principal_holders": len(principal.get("holders", [])),
            "disclosed_fund_exposure_usd": total_exposure,
            "insider_voting_pct": top_voting,
        },
        "sources": sources,
        "notes": [
            "Pre-IPO, IPO share allocations are private and not publicly "
            "disclosed in real time. 'Fund holders' are registered funds that "
            "already report a SpaceX position in their NPORT-P filings.",
            "Most SpaceX equity is held privately — by insiders, employees, and "
            "venture investors — and never appears in fund filings. The S-1 "
            "'Security Ownership' table ('Insiders & 5% owners') discloses every "
            "holder of more than 5% of a share class, plus officers and directors.",
            "Retirement exposure: many funds here are common 401(k)/IRA options "
            "(tickers shown), and target-date funds can hold SpaceX indirectly "
            "(see 'Retirement & 401(k)').",
            "'Institutional buyers (13F)' populates after the first 13F-HR "
            "filings covering the IPO quarter post, ~mid-August 2026.",
            "Fund figures are aggregated across share classes from the most "
            "recent NPORT-P period available; implied dollar values under "
            "'Insiders & 5% owners' are share counts times the expected IPO "
            "price, not market values. Every row links to its source filing.",
        ],
        "disclaimer": "Built from public SEC filings, which post on a delay. "
                      "For information and transparency only — not investment "
                      "advice, an offer, or a solicitation.",
    }

    def write(name, obj):
        path = os.path.join(OUT_DIR, name)
        with open(path, "w") as fh:
            json.dump(obj, fh, indent=2)
        print(f"  wrote {name}")

    write("issuer_filings.json", issuer)
    write("fund_holders.json", holders)
    write("institutional_13f.json", f13)
    write("principal_holders.json", principal)
    write("meta.json", meta)
    print("\nDone.")


if __name__ == "__main__":
    main()
