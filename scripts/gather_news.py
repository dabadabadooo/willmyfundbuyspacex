#!/usr/bin/env python3
"""
gather_news.py - collect candidate news items about who is buying / committing
to the SpaceX IPO, for HUMAN REVIEW.

This intentionally does NOT write into site/data. The public site only shows
hand-vetted items in site/data/commitments.json. This script writes a review
queue to ../review/news_candidates.json; a human promotes good, properly
attributed items into commitments.json. Rationale: a public transparency feed
must not auto-publish unvetted headlines (scams, rumor, copyright, defamation).

Sources used are free and keyless: Google News RSS and the GDELT 2.0 Doc API.
stdlib only. Set SEC_USER_AGENT or NEWS_USER_AGENT to a real contact string.
"""
import os, re, json, html, datetime as dt, urllib.parse
from urllib.request import Request, urlopen

UA = (os.environ.get("NEWS_USER_AGENT")
      or os.environ.get("SEC_USER_AGENT")
      or "SpaceX-IPO-Transparency (set NEWS_USER_AGENT to a real contact)")

REVIEW_DIR = os.path.join(os.path.dirname(__file__), "..", "review")

QUERIES = [
    '"SpaceX IPO" allocation',
    '"SpaceX IPO" anchor investor',
    '"SpaceX IPO" cornerstone',
    '"SpaceX IPO" (pension OR "sovereign wealth")',
    'SPCX SpaceX fund buy',
]


def _get(url, timeout=30):
    try:
        req = Request(url, headers={"User-Agent": UA,
                                    "Accept-Encoding": "gzip"})
        r = urlopen(req, timeout=timeout)
        raw = r.read()
        if "gzip" in r.headers.get("Content-Encoding", ""):
            import gzip
            raw = gzip.decompress(raw)
        return raw.decode("utf-8", "ignore")
    except Exception as e:
        print(f"  ! fetch failed: {e}")
        return None


def _clean(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def google_news(query):
    url = ("https://news.google.com/rss/search?q="
           + urllib.parse.quote(query)
           + "&hl=en-US&gl=US&ceid=US:en")
    xml = _get(url)
    out = []
    if not xml:
        return out
    for m in re.finditer(r"<item>(.*?)</item>", xml, re.S):
        blk = m.group(1)
        title = _clean((re.search(r"<title>(.*?)</title>", blk, re.S) or [None, ""])[1])
        link = _clean((re.search(r"<link>(.*?)</link>", blk, re.S) or [None, ""])[1])
        pub = _clean((re.search(r"<pubDate>(.*?)</pubDate>", blk, re.S) or [None, ""])[1])
        src = _clean((re.search(r"<source[^>]*>(.*?)</source>", blk, re.S) or [None, ""])[1])
        if link:
            out.append({"title": title, "url": link, "source": src,
                        "published": pub, "query": query, "via": "google_news"})
    return out


def gdelt(query):
    url = ("https://api.gdeltproject.org/api/v2/doc/doc?query="
           + urllib.parse.quote(query)
           + "&mode=ArtList&format=json&maxrecords=50&timespan=21d")
    txt = _get(url)
    out = []
    if not txt:
        return out
    try:
        data = json.loads(txt)
    except Exception:
        return out
    for a in data.get("articles", []):
        if a.get("url"):
            out.append({"title": a.get("title", ""), "url": a["url"],
                        "source": a.get("domain", ""),
                        "published": a.get("seendate", ""),
                        "query": query, "via": "gdelt"})
    return out


def main():
    os.makedirs(REVIEW_DIR, exist_ok=True)
    seen, candidates = set(), []
    for q in QUERIES:
        print(f"query: {q}")
        for fn in (google_news, gdelt):
            for item in fn(q):
                u = item["url"]
                if u in seen:
                    continue
                seen.add(u)
                candidates.append(item)
    candidates.sort(key=lambda x: x.get("published", ""), reverse=True)

    payload = {
        "generated_utc": dt.datetime.now(dt.timezone.utc)
                           .replace(microsecond=0).isoformat(),
        "note": ("REVIEW QUEUE - not published. A human must vet each item for "
                 "credibility, paraphrase it, tag a confidence level, and copy "
                 "it into site/data/commitments.json. Do not publish raw."),
        "queries": QUERIES,
        "count": len(candidates),
        "candidates": candidates,
    }
    out_path = os.path.join(REVIEW_DIR, "news_candidates.json")
    with open(out_path, "w") as fh:
        json.dump(payload, fh, indent=2)
    print(f"\nwrote {len(candidates)} candidates -> {os.path.normpath(out_path)}")


if __name__ == "__main__":
    main()
