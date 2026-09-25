"""Collect each brand's live ads from Google Ads Transparency Center and Meta Ad Library.

Reads brands from sa_brands.txt and, for each one:
  - Google: opens adstransparency.google.com filtered to the brand's domain and region,
    scrolls to load every creative, saves the creative images and a screenshot of each card.
  - Meta:   opens the Facebook Ad Library keyword search for the brand name in the region,
    scrolls, saves ad images/videos, the ad text and the landing link.

Usage:
  python casino_ad_scraper/ad_libraries.py                     # all brands, both libraries
  python casino_ad_scraper/ad_libraries.py --source google --brands Betway Hollywoodbets --headed
"""
import argparse, csv, hashlib, json, os, re, sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

import requests
from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36"
MEDIA_EXT = re.compile(r"\.(jpe?g|png|gif|webp|mp4|webm)(\?|$)", re.I)


def load_brands():
    rows = [l.split("|") for l in (HERE / "sa_brands.txt").read_text().splitlines()
            if "|" in l and not l.startswith("#")]
    return [(n.strip(), d.strip()) for n, d in rows]


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def download(url, dest: Path, sess):
    try:
        r = sess.get(url, timeout=30)
        r.raise_for_status()
    except Exception as e:
        return None
    m = MEDIA_EXT.search(url)
    ext = m.group(1) if m else r.headers.get("content-type", "bin").split("/")[-1].split(";")[0]
    path = dest / f"{hashlib.sha1(r.content).hexdigest()[:16]}.{ext}"
    if not path.exists():
        path.write_bytes(r.content)
    return path


def scroll_all(page, max_rounds=40, pause=1500):
    """Scroll until the page stops growing (all results loaded) or max_rounds is hit."""
    last = 0
    for _ in range(max_rounds):
        page.mouse.wheel(0, 3000)
        page.wait_for_timeout(pause)
        h = page.evaluate("document.body.scrollHeight")
        if h == last:
            break
        last = h


def google(page, name, domain, region, out, sess):
    url = f"https://adstransparency.google.com/?region={region}&domain={domain}"
    print(f"  google: {url}")
    page.goto(url, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(3000)
    scroll_all(page)
    cards = page.query_selector_all("creative-preview, priority-creative-grid creative-preview, a[href*='/creative/']")
    ads = []
    for i, card in enumerate(cards):
        link = card.get_attribute("href") or (card.query_selector("a[href*='/creative/']") or card).get_attribute("href")
        imgs = [e.get_attribute("src") for e in card.query_selector_all("img")]
        # Many Google creatives render inside iframes; pull images from those too.
        for f in card.query_selector_all("iframe"):
            try:
                fr = f.content_frame()
                imgs += fr.eval_on_selector_all("img", "es => es.map(e => e.src)") if fr else []
            except Exception:
                pass
        shot = out / "screenshots" / f"google-{slug(name)}-{i:03d}.png"
        try:
            card.screenshot(path=str(shot), timeout=5000)
        except Exception:
            shot = None
        files = [str(p) for p in (download(u, out / "creatives", sess) for u in imgs if u and u.startswith("http")) if p]
        ads.append({"source": "google", "brand": name, "domain": domain,
                    "ad_url": f"https://adstransparency.google.com{link}" if link and link.startswith("/") else link,
                    "creative_urls": imgs, "files": files, "screenshot": str(shot) if shot else None, "text": ""})
    return ads


META_JS = """
() => [...document.querySelectorAll('div')].filter(d => /Library ID/.test(d.innerText) && d.innerText.length < 4000
        && ![...d.children].some(c => /Library ID/.test(c.innerText))).map(d => {
  const card = d.closest('div[class]').parentElement || d;
  return {
    id: (d.innerText.match(/Library ID:?\\s*(\\d+)/) || [])[1] || null,
    text: card.innerText.slice(0, 1500),
    images: [...card.querySelectorAll('img')].map(i => i.src).filter(s => s.includes('scontent')),
    videos: [...card.querySelectorAll('video')].map(v => v.src || v.poster).filter(Boolean),
    links: [...card.querySelectorAll('a[href]')].map(a => a.href).filter(h => h.includes('l.facebook.com') || !h.includes('facebook.com')),
  };
})
"""


def meta(page, name, region, out, sess):
    url = ("https://www.facebook.com/ads/library/?active_status=active&ad_type=all"
           f"&country={region}&q={quote_plus(name)}&search_type=keyword_unordered&media_type=all")
    print(f"  meta:   {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)
    for label in ("Allow all cookies", "Only allow essential cookies", "Decline optional cookies"):
        btn = page.query_selector(f"text={label}")
        if btn:
            btn.click(); page.wait_for_timeout(1000); break
    scroll_all(page, max_rounds=25, pause=2500)
    ads, seen = [], set()
    for card in page.evaluate(META_JS):
        if not card["id"] or card["id"] in seen:
            continue
        seen.add(card["id"])
        media = card["images"] + card["videos"]
        files = [str(p) for p in (download(u, out / "creatives", sess) for u in media) if p]
        ads.append({"source": "meta", "brand": name, "domain": "",
                    "ad_url": f"https://www.facebook.com/ads/library/?id={card['id']}",
                    "creative_urls": media, "files": files, "screenshot": None,
                    "text": card["text"], "landing_urls": card["links"]})
    return ads


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["google", "meta", "both"], default="both")
    ap.add_argument("--region", default="ZA", help="2-letter country code")
    ap.add_argument("--brands", nargs="*", help="only these brand names (default: all in sa_brands.txt)")
    ap.add_argument("--out", default="output")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    brands = load_brands()
    if args.brands:
        want = {b.lower() for b in args.brands}
        brands = [b for b in brands if b[0].lower() in want]
    run = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out) / f"libraries-{run}"
    (out / "creatives").mkdir(parents=True, exist_ok=True)
    (out / "screenshots").mkdir(exist_ok=True)
    sess = requests.Session(); sess.headers["User-Agent"] = UA

    all_ads = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed, executable_path=os.environ.get("CHROMIUM_PATH") or None)
        page = browser.new_context(user_agent=UA, viewport={"width": 1366, "height": 900}, locale="en-ZA").new_page()
        done_domains = set()
        for name, domain in brands:
            print(f"-> {name}")
            if args.source in ("google", "both") and domain not in done_domains:
                done_domains.add(domain)
                try:
                    all_ads += google(page, name, domain, args.region, out, sess)
                except Exception as e:
                    print(f"  ! google failed: {e.__class__.__name__}: {e}", file=sys.stderr)
            if args.source in ("meta", "both"):
                try:
                    all_ads += meta(page, name, args.region, out, sess)
                except Exception as e:
                    print(f"  ! meta failed: {e.__class__.__name__}: {e}", file=sys.stderr)
            print(f"   total ads so far: {len(all_ads)}")
            (out / "ads.json").write_text(json.dumps(all_ads, indent=2))
        browser.close()

    with open(out / "ads.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["source", "brand", "ad_url", "landing_urls", "creative_url", "file", "screenshot", "text"])
        for ad in all_ads:
            for i, u in enumerate(ad["creative_urls"] or [""]):
                w.writerow([ad["source"], ad["brand"], ad["ad_url"], " | ".join(ad.get("landing_urls", [])), u,
                            ad["files"][i] if i < len(ad["files"]) else "", ad["screenshot"] or "",
                            ad["text"][:300].replace("\n", " ")])
    print(f"\nDone: {len(all_ads)} ads saved to {out}")


if __name__ == "__main__":
    main()
