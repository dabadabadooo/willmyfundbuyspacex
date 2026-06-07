# SPCX Fund Tracker — who owns SpaceX

A public, source-linked website tracking the funds with disclosed **SpaceX**
exposure and — after listing — the institutions that buy **SPCX**. Every number
on the page links to its underlying filing on SEC EDGAR.

It is a static site backed by a scheduled data fetch. No server, no database.

```
  SEC EDGAR  ──►  scripts/fetch_edgar.py  ──►  site/data/*.json  ──►  static site
 (filings API,     (run by GitHub Actions        (committed back        (site/index.html
  full-text search)  cron, every 6h)              to the repo)           reads the JSON)
```

When the cron commits new JSON, your static host redeploys automatically.

---

## What it tracks (and the honest caveats)

- **Fund holders** — registered funds (mutual funds, ETFs, closed-end funds)
  that already report a *Space Exploration Technologies Corp.* position in their
  **NPORT-P** filings, aggregated across share classes per fund and ranked by
  position value. This is real, current exposure today.
- **SpaceX filings** — SpaceX's own EDGAR filings: the S-1, its amendments,
  free-writing prospectuses, and (once filed) the final priced 424B.
- **Institutional buyers (13F)** — institutions managing over $100M disclose
  holdings on **Form 13F-HR** within 45 days of quarter-end. The IPO lands in
  Q2 2026, so the first 13Fs naming SPCX are expected around **mid-August 2026**,
  then refresh quarterly. The query is already wired up; the tab is empty until
  then.

**Important:** pre-IPO, the actual IPO *allocations* (who is promised shares) are
private and are **not** publicly disclosed in real time. Any site claiming to
list "confirmed pre-IPO buyers" or asking you to "submit details for an
allocation" is not a data source. This project only reports what is in public
SEC filings, which post on a delay. **Not investment advice.**

The "Expected offering" figures (price, raise, valuation, dates) come from the
prospectus and press reporting and are shown as *expected*. Update them in
`scripts/fetch_edgar.py` (`IPO_FACTS`) once the final 424B prices.

---

## Local run

Requires Python 3.9+ (standard library only — no dependencies).

```bash
# SEC requires a descriptive User-Agent with real contact info.
export SEC_USER_AGENT="SPCX Fund Tracker you@yourdomain.com"

python3 scripts/fetch_edgar.py        # writes site/data/*.json

# preview the site locally
cd site && python3 -m http.server 8000   # open http://localhost:8000
```

Opening `site/index.html` directly from disk also works — if the `fetch()`
calls fail, the page falls back to an embedded snapshot baked into the HTML.

Tuning (optional env vars): `MAX_HITS` (full-text hits scanned, default 400),
`MAX_FETCH` (filings parsed, default 320).

---

## Deploy

The publish directory is **`site/`** (it contains `index.html` and `data/`).

**Cloudflare Pages / Netlify / Vercel (recommended):**
1. Push this repo to GitHub.
2. Create a new project from the repo.
3. Build command: *none*. Output / publish directory: **`site`**.
4. These hosts auto-redeploy on every push, so the cron's data commits go live
   automatically.

**GitHub Pages:** Pages serves from the repo root or `/docs`, not `/site`.
Either rename `site/` to `docs/` (and update `OUT_DIR` in the fetcher), or add a
Pages deploy action. A push-redeploy host above is simpler.

### Turn on auto-updates

1. **Settings → Secrets and variables → Actions → Variables → New variable**
   - Name: `SEC_USER_AGENT`
   - Value: `Your Project you@yourdomain.com`  (real contact — SEC policy)
2. The workflow in `.github/workflows/update-data.yml` runs every 6 hours and on
   manual trigger (**Actions → Update SpaceX fund data → Run workflow**). It
   commits to `site/data/` only when something changed.

---

## Repo layout

```
scripts/fetch_edgar.py          # EDGAR fetcher (stdlib only)
site/index.html                 # single-file front end (fetches ./data, embedded fallback)
site/data/*.json                # generated data: issuer_filings, fund_holders, institutional_13f, meta
.github/workflows/update-data.yml
```

## Data sources

- SpaceX filings — SEC EDGAR submissions API, CIK `1181412` (ticker `SPCX`,
  offering file no. `333-296070`).
- Fund holders — SEC EDGAR full-text search over `NPORT-P` for both names the
  position is filed under ("Space Exploration Technologies" and "SpaceX"),
  parsed and aggregated per fund from each filing's structured XML.
- All figures link to the source filing.

Built from public data for transparency only — not investment advice, an offer,
or a solicitation.

## Reported commitments & news (separate from filings)

The "Reported commitments" tab (`site/data/commitments.json`) tracks who has
*publicly stated or been reported* to be buying or committing to the SpaceX IPO
— anchor/cornerstone investors, brokerages securing retail allocations, ETFs
declaring intent, corporate holders, etc. This is **distinct from the verified
filing data**: IPO allocations are confidential, so these are claims, tagged by
source credibility (`official` / `reported` / `unconfirmed`) and linked to their
source. It is hand-curated and never auto-published.

`scripts/gather_news.py` builds a **review queue** at `review/news_candidates.json`
from free, keyless sources (Google News RSS + GDELT). It is intentionally NOT in
`site/` and is never shown publicly. Workflow: run it, vet each candidate for
credibility, paraphrase (do not copy — respect copyright), tag a confidence
level, and copy good items into `commitments.json`.

```
NEWS_USER_AGENT="Your Project you@domain.com" python3 scripts/gather_news.py
```

Guardrails: never auto-publish scraped headlines; attribute everything; never
link to or amplify "pre-IPO allocation" solicitation sites (they are scams).
