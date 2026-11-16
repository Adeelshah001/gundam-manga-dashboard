#!/usr/bin/env python3
"""
Gundam Manga - Buy-Link Verifier.

For every volume in data/gundam_manga.json, checks whether its buy links
actually resolve to the book on each storefront, and records the result:

    "links": [
      {"store": "amazon", "label": "Amazon",
       "url": ..., "status": "available|unavailable|unknown", "reason": ...},
      ...
    ],
    "linksCheckedAt": "<ISO-8601 UTC timestamp>"

Each volume carries only the stores that carry it (a flexible array, not
a fixed set). The checker verifies the stores listed; it never adds a
store. The frontend (index.html) renders only links whose status is
"available" or "unknown", and hides "unavailable" ones.

Link policy: the default is the exact product page for the volume. Only
when a provider cannot land on the specific product page does the stored
URL fall back to a store search for the exact volume, queried as
"<title> manga" so results stay on the manga edition rather than other
Gundam merchandise. A link is hidden only when the provider is confirmed
not to carry the book (see "Fail open" below).

Design principles:

* Fail open. A link is hidden ONLY on a confirmed negative signal. Any
  inconclusive outcome (bot challenge, timeout, proxy error, unrecognized
  page) keeps the link visible with status "unknown", so a failed check
  can never nuke a good link. A previously verified exact product page is
  never downgraded on an inconclusive re-check ("exact-kept").
* Penguin Random House (distributes Kodansha USA): the /isbn/{isbn} URL
  format returns 404 for every title tested (even with a verified-correct
  ISBN), so it cannot be used as a negative signal on its own. PRH is
  checked via the Random House reststop title API plus the /isbn/ page;
  "unavailable" requires BOTH to 404. A previously verified canonical
  /books/ page is re-fetched each run and kept while it still resolves
  to the book. (Mechanism probed 2026-11-14; reststop was flaky from the
  VM on 2026-11-15, which the circuit breaker treats as inconclusive.)
* Indigo: indigo.ca is a Shopify storefront. Its public suggest API
  (/search/suggest.json) is queried three times per volume: by ISBN, by
  title, and by title + "manga". Candidates fuzzy-match, so they are
  verified by normalized title match (overlap or subset containment,
  with volume numerals enforced), so Vol. 1 never matches Vol. 2. A
  title match in any query = "available" with the exact product page.
  "Unavailable" requires BOTH title-shaped queries to return candidates
  with no match. Empty/failed queries = "unknown". A stored exact
  product page is re-fetched first and kept when it still resolves;
  Indigo full pages 403 from datacenter IPs, which is inconclusive, not
  a negative. (Probed 2026-11-15: the ISBN query returned the exact
  Thunderbolt Vol. 1 product on the first try.)
* Amazon: amazon.com serves an interstitial bot challenge to datacenter
  IPs, so plain requests cannot verify availability. Amazon checks record
  "unknown" when the challenge page is detected. A stored exact product
  page (/dp/ or /gp/product/) is kept verbatim ("exact-kept") and never
  rewritten to a search. Otherwise the stored URL is a title search on
  amazon.com; the frontend swaps in amazon.ca for visitors in Canada.
* Bookshop.org: product pages and search return Cloudflare 403s to
  datacenter IPs, and product URLs carry an opaque id, so availability
  cannot be verified from a server. Bookshop links are recorded
  "unknown" with a title search URL, the same fail-open treatment as
  Amazon. An exact /p/books/ page is kept verbatim ("exact-kept").
* Publisher & other storefronts (VIZ, Yen Press, Denpa, Kodansha,
  Barnes & Noble, and any future store): the stored exact page is
  re-fetched. 200 + the ISBN in the body, or a title match on the page
  <title>, = "available". Only a 404 hides the link. Anything else is
  "unknown" with the URL kept. (Probed 2026-11-15: VIZ, Yen Press, and
  Denpa pages all return 200 with the ISBN and title in the HTML.
  Denpa's Beltorchika Vol. 1 page lists the digital ISBN, not the print
  ISBN in the catalog, so the title-match fallback is what verifies it.)
* Owner-pinned hides. An entry may carry "hideLinks": ["prh"] (any store
  names); those stores are recorded "unavailable" with reason
  "hidden-by-owner" regardless of the live check, so a deliberate hide
  survives every daily run. Use only when absence is established outside
  the check.

Stdlib only (no pip dependencies), so it runs unchanged in GitHub Actions.

Usage:
    python check_buy_links.py [--limit N] [--delay SECONDS]
"""

import argparse
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "gundam_manga.json")

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# HTTP helpers (stdlib only)
# ---------------------------------------------------------------------------

def fetch(url, timeout=15, accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"):
    """GET a URL, following redirects. Returns (status, final_url, body_text).

    HTTP error statuses (404, 403, ...) are returned, not raised, so callers
    can use the status as a signal. Network-level failures raise URLError.
    """
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": BROWSER_UA,
            "Accept": accept,
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.status, resp.geturl(), raw.decode(charset, errors="replace")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return e.code, e.geturl(), body


def fetch_with_retry(url, timeout=15, accept=None, retries=1, backoff=2.0):
    """fetch() with one retry on transient network errors."""
    kwargs = {"timeout": timeout}
    if accept is not None:
        kwargs["accept"] = accept
    last_exc = None
    for attempt in range(retries + 1):
        try:
            return fetch(url, **kwargs)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last_exc = e
            if attempt < retries:
                time.sleep(backoff)
    raise last_exc


def page_title(body):
    """Normalized <title> text of an HTML body, or ''."""
    m = re.search(r"<title>(.*?)</title>", body or "", re.S)
    if not m:
        return ""
    title = re.sub(r"\s+", " ", m.group(1)).strip()
    return re.sub(r"&#x27;|&apos;|&#39;", "'", title)


# ---------------------------------------------------------------------------
# Title matching (Indigo, exact-page checks)
# ---------------------------------------------------------------------------

STOPWORDS = {
    "the", "and", "of", "a", "an", "vol", "volume", "book", "part",
    "edition", "with", "in", "to", "for", "manga",
}
ROMAN_NUMERALS = {"i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"}


def title_tokens(title):
    """Distinctive lowercase tokens: long words plus volume numerals."""
    cleaned = re.sub(r"[^a-z0-9 ]", " ", (title or "").lower())
    out = []
    for tok in cleaned.split():
        if tok in STOPWORDS:
            continue
        if len(tok) > 2 or tok.isdigit() or tok in ROMAN_NUMERALS:
            out.append(tok)
    return out


def titles_match(catalog_title, product_title):
    """True when a storefront product title is plausibly the same book.

    Every volume numeral in the catalog title (e.g. "2" in "Vol. 2")
    must appear in the product title, so Vol. 1 never matches Vol. 2.
    Beyond that, a match needs either >=60% of the catalog title's
    distinctive tokens present, or one title's distinctive tokens fully
    contained in the other's (a store title is often a shortened form).
    Containment requires at least 2 shared tokens, so a single shared
    word can never match.
    """
    ctoks = set(title_tokens(catalog_title))
    ptoks = set(title_tokens(product_title))
    if not ctoks or not ptoks:
        return False
    numerals = [t for t in ctoks if t.isdigit() or t in ROMAN_NUMERALS]
    if numerals and any(n not in ptoks for n in numerals):
        return False
    shared = ctoks & ptoks
    if len(shared) / len(ctoks) >= 0.6:
        return True
    return len(shared) >= 2 and (ctoks <= ptoks or ptoks <= ctoks)


# ---------------------------------------------------------------------------
# Storefront checks. Each returns (status, url, reason).
# ---------------------------------------------------------------------------

# Circuit breaker: if reststop is unreachable from this runner, it will be
# unreachable for every title. After two consecutive network failures we
# stop trying it for the rest of the run instead of paying a timeout per
# title. A clean HTTP response (including 404) resets the counter.
_reststop_dead = False
_reststop_failures = 0


_CANONICAL_PRH_RE = re.compile(r"^https://(www\.)?penguinrandomhouse\.com/books/\d+/")


def search_query(title):
    """Store search query for the manga edition of a volume.

    Exact product pages are always preferred; this query is only the
    fallback when a provider cannot land on the specific product page.
    Appending "manga" keeps results on the manga edition instead of
    other Gundam merchandise carrying the same name.
    """
    return f"{title} manga"


def check_prh(isbn, title, existing_url=None):
    global _reststop_dead, _reststop_failures
    search_url = (
        "https://www.penguinrandomhouse.com/search/"
        + urllib.parse.quote(search_query(title))
    )
    existing_canonical = (
        existing_url if existing_url and _CANONICAL_PRH_RE.match(existing_url) else None
    )
    reststop_404 = False
    page_gone = False

    # 0. Re-verify a previously verified canonical book page. While it
    #    still resolves to this book, it stays the stored link. A 404
    #    here does NOT drop the page on its own: PRH can 404 /books/
    #    pages to datacenter IPs while the page exists for humans, so a
    #    lone 404 is inconclusive. Dropping a canonical requires the
    #    triple negative below.
    if existing_canonical:
        try:
            status, _, body = fetch(existing_canonical, timeout=15)
            head = title_tokens(title)[:1]
            if status == 200 and (
                isbn in (body or "") or (head and head[0] in (body or "").lower())
            ):
                return "available", existing_canonical, "canonical-verified"
            if status == 404:
                page_gone = True
        except Exception:
            pass  # Inconclusive: keep it as the fallback below.

    # 1. Random House reststop title API (authoritative when reachable).
    if not _reststop_dead:
        try:
            status, _, body = fetch(
                f"https://reststop.randomhouse.com/resources/titles/{isbn}",
                accept="application/json",
                timeout=6,
            )
            _reststop_failures = 0
            if status == 200 and body:
                head = title_tokens(title)[:1]
                if isbn in body or (head and head[0] in body.lower()):
                    m = re.search(
                        r"https://www\.penguinrandomhouse\.com/books/\d+/[^\"'\s<]+",
                        body,
                    )
                    if m:
                        return "available", m.group(0), "reststop"
                    if existing_canonical:
                        return "available", existing_canonical, "canonical-kept"
                    return "available", search_url, "reststop"
            elif status == 404:
                reststop_404 = True
        except Exception:
            # Unreachable from this runner: inconclusive, not a negative.
            _reststop_failures += 1
            if _reststop_failures >= 2:
                _reststop_dead = True

    # 2. The /isbn/ page. (404s for everything as of 2026-11-14, but cheap
    #    to keep checking in case PRH fixes the route.)
    isbn_404 = False
    try:
        status, final_url, body = fetch_with_retry(
            f"https://www.penguinrandomhouse.com/isbn/{isbn}", timeout=15
        )
        if status == 200 and "Page Not Found" not in page_title(body):
            return "available", final_url, "isbn-page"
        isbn_404 = status == 404
    except Exception:
        pass

    # A verified canonical page is stronger evidence than the two lookup
    # routes (the /isbn/ route 404s even for books PRH definitely
    # carries, and /books/ pages can 404 to datacenter IPs), so only a
    # triple negative drops it. An inconclusive run never downgrades it.
    if existing_canonical:
        if page_gone and reststop_404 and isbn_404:
            return "unavailable", search_url, "canonical-404+reststop-404+isbn-404"
        return "available", existing_canonical, "canonical-kept"

    # Only two independent negative signals may hide the link.
    if reststop_404 and isbn_404:
        return "unavailable", search_url, "reststop-404+isbn-404"
    return "unknown", search_url, "unverifiable"


def _indigo_suggest(query):
    """Shopify suggest API. Returns a product list, or None on any failure."""
    url = (
        "https://www.indigo.ca/search/suggest.json?q="
        + urllib.parse.quote(query)
        + "&resources[type]=product&resources[limit]=10"
    )
    try:
        status, _, body = fetch_with_retry(url, accept="application/json", timeout=15)
    except Exception:
        return None
    if status != 200:
        return None
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return None
    return data.get("resources", {}).get("results", {}).get("products", [])


def _best_indigo_match(title, products):
    """First title-matching product."""
    for p in products:
        if titles_match(title, p.get("title", "")):
            return p
    return None


def _is_indigo_product_url(url):
    return bool(url) and "indigo.ca" in url and "/products/" in url


def check_indigo(isbn, title, existing_url=None):
    search_url = "https://www.indigo.ca/en-ca/search?q=" + urllib.parse.quote_plus(
        search_query(title)
    )
    existing_product = existing_url if _is_indigo_product_url(existing_url) else None
    page_gone = False

    # 0. Re-verify a stored exact product page. Indigo full pages 403
    #    from datacenter IPs, which is inconclusive (fall through), not
    #    a negative. Only a 404 marks the page gone.
    if existing_product:
        try:
            status, _, body = fetch(existing_product, timeout=15)
            if status == 200 and (isbn in (body or "") or titles_match(title, page_title(body))):
                return "available", existing_product, "exact-verified"
            if status == 404:
                page_gone = True
        except Exception:
            pass

    isbn_products = _indigo_suggest(isbn)
    time.sleep(0.4)
    title_products = _indigo_suggest(title)
    time.sleep(0.4)
    manga_products = _indigo_suggest(title + " manga")

    all_products = []
    for products in (isbn_products, title_products, manga_products):
        if products:
            all_products.extend(products)
    match = _best_indigo_match(title, all_products)
    if match:
        path = (match.get("url") or "").split("?")[0]
        url = f"https://www.indigo.ca{path}" if path.startswith("/") else search_url
        return "available", url, "title-match"

    # Both title-shaped queries returned candidates but none matched: the
    # book genuinely is not surfacing. An empty/failed query on either
    # side is inconclusive, not a negative.
    if title_products and manga_products:
        if existing_product and not page_gone:
            # A previously verified exact page outweighs a suggest miss
            # (the re-check above was inconclusive, e.g. a 403 bot wall).
            return "available", existing_product, "exact-kept"
        return "unavailable", search_url, "no-title-match"
    if existing_product:
        return "available", existing_product, "exact-kept"
    return "unknown", search_url, "no-candidates"


AMAZON_CHALLENGE_MARKERS = (
    "triggerInterstitialChallenge",
    "Enter the characters",
    "automated access",
    "api-services-support@amazon",
)


def _is_amazon_product_url(url):
    return bool(url) and ("amazon.com" in url or "amazon.ca" in url) and (
        "/dp/" in url or "/gp/product/" in url or "/exec/obidos/" in url
    )


def check_amazon(isbn, title, existing_url=None):
    # A stored exact product page can never be verified from a server
    # (Amazon's bot challenge blocks every check), so it is kept
    # verbatim and never rewritten to a search.
    if _is_amazon_product_url(existing_url):
        return "unknown", existing_url, "exact-kept"
    # Title search. The "manga" qualifier keeps it on this edition.
    # (The frontend swaps amazon.com for amazon.ca for visitors in
    # Canada.)
    url = "https://www.amazon.com/s?k=" + urllib.parse.quote_plus(search_query(title))
    try:
        status, _, body = fetch_with_retry(url, timeout=15)
    except Exception:
        return "unknown", url, "request-error"
    lowered = (body or "").lower()
    if any(m.lower() in lowered for m in AMAZON_CHALLENGE_MARKERS):
        return "unknown", url, "bot-challenge"
    if "no results for" in lowered or "no results found" in lowered:
        return "unavailable", url, "no-results"
    if "s-search-result" in body:
        return "available", url, "results-found"
    return "unknown", url, "unrecognized-page"


def check_bookshop(isbn, title, existing_url=None):
    # Title search. Bookshop.org cannot be verified from a server: its
    # product pages and search return Cloudflare 403s to datacenter IPs,
    # and product URLs carry an opaque id that cannot be built from the
    # ISBN, so no request is made and the search is recorded "unknown"
    # (fail-open, same treatment as Amazon). If the store ever becomes
    # fetchable, this is the one place to add a live check.
    #
    # An exact /p/books/ page is kept verbatim (it cannot be re-verified,
    # so it stays "unknown" rather than being downgraded back to a search).
    if existing_url and existing_url.startswith("https://bookshop.org/p/books/"):
        return "unknown", existing_url, "exact-kept"
    url = "https://bookshop.org/beta-search?keywords=" + urllib.parse.quote_plus(search_query(title))
    return "unknown", url, "unverifiable"


def check_exact_page(url, isbn, title):
    """Generic check for publisher & other storefronts (VIZ, Yen, Denpa,
    Kodansha, Barnes & Noble, and any future store).

    The stored exact page is re-fetched: 200 + the ISBN in the body, or
    a title match on the page <title>, means the volume is there. Only a
    404 hides the link; anything else keeps it with "unknown".
    """
    if not url:
        return "unknown", "", "no-url"
    try:
        status, _, body = fetch_with_retry(url, timeout=15)
    except Exception:
        return "unknown", url, "request-error"
    if status == 200 and (isbn in (body or "") or titles_match(title, page_title(body))):
        return "available", url, "page-verified"
    if status == 404:
        return "unavailable", url, "page-404"
    return "unknown", url, "unverifiable"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_data():
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data(data):
    # Preserve file order (the sorter script owns sorting).
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def check_link(store, url, isbn, title):
    """Dispatch one stored link to its storefront check."""
    if store == "amazon":
        return check_amazon(isbn, title, url)
    if store == "indigo":
        return check_indigo(isbn, title, url)
    if store == "prh":
        return check_prh(isbn, title, url)
    if store == "bookshop":
        return check_bookshop(isbn, title, url)
    return check_exact_page(url, isbn, title)


def check_item(item, delay):
    """Re-verify one entry's stored links in place. Returns (counts, hidden)."""
    counts = {"available": 0, "unavailable": 0, "unknown": 0}
    hidden = []
    isbn = (item.get("isbn") or "").strip()
    title = item.get("title", "")
    pinned = set(item.get("hideLinks") or [])
    new_links = []
    for entry in item.get("links") or []:
        store = entry.get("store")
        if not store:
            continue
        url = entry.get("url") or ""
        if store in pinned:
            # Owner-pinned hide: skip the live check entirely so the
            # stored URL is preserved verbatim if the pin is ever lifted.
            status, new_url, reason = "unavailable", url, "hidden-by-owner"
        elif isbn:
            status, new_url, reason = check_link(store, url, isbn, title)
            time.sleep(delay)
        else:
            status, new_url, reason = "unknown", url, "no-isbn"
        new_entry = {"store": store, "url": new_url, "status": status, "reason": reason}
        if entry.get("label"):
            new_entry["label"] = entry["label"]
        new_links.append(new_entry)
        counts[status] += 1
        if status == "unavailable":
            hidden.append(f"{title} [{store}]")
    item["links"] = new_links
    return counts, hidden


def main():
    parser = argparse.ArgumentParser(description="Verify buy links for Gundam Manga.")
    parser.add_argument("--limit", type=int, default=0, help="Check only the first N volumes (0 = all).")
    parser.add_argument("--delay", type=float, default=0.7, help="Seconds to wait between requests.")
    args = parser.parse_args()

    data = load_data()
    items = data[: args.limit] if args.limit else data
    checked_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    counts = {"available": 0, "unavailable": 0, "unknown": 0}
    hidden = []

    for idx, item in enumerate(items, 1):
        item_counts, item_hidden = check_item(item, args.delay)
        for st, n in item_counts.items():
            counts[st] += n
        hidden.extend(item_hidden)
        if item.get("links"):
            item["linksCheckedAt"] = checked_at
        summary = " ".join(f"{e['store']}={e['status']}" for e in item.get("links") or [])
        print(
            f"[{idx}/{len(items)}] {item.get('title', '')[:52]:<52} {summary or '(no links)'}",
            flush=True,
        )

    save_data(data)
    total = sum(counts.values())
    print(f"\nChecked {len(items)} volumes ({total} store links) at {checked_at}")
    print(f"  available: {counts['available']}  unavailable (hidden): {counts['unavailable']}  unknown (kept): {counts['unknown']}")
    if hidden:
        print("\nLinks that will be HIDDEN (confirmed unavailable):")
        for h in hidden:
            print(f"  - {h}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
