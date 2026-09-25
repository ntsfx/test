"""Crawl publisher pages, capture programmatic ad creatives, keep casino/gambling ones.

For each page:
  1. Load it in headless Chromium and scroll to trigger lazy-loaded ad slots.
  2. Record every response served from a known ad-tech domain.
  3. Walk ad iframes: collect creative images/videos, click-through URLs, and text.
  4. Classify as casino if landing URL / text / alt / creative URL hits a keyword.
  5. Download the creative files, screenshot the ad slot, write a manifest.

Usage:
  python casino_ad_scraper/scraper.py                      # uses publishers.txt
  python casino_ad_scraper/scraper.py --urls https://site.com --all
"""
import argparse, csv, os, hashlib, json, re, sys, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

import requests
from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent

AD_DOMAINS = (
    "doubleclick.net", "googlesyndication.com", "googleadservices.com", "adnxs.com",
    "rubiconproject.com", "pubmatic.com", "openx.net", "criteo.com", "casalemedia.com",
    "amazon-adsystem.com", "adsrvr.org", "taboola.com", "outbrain.com", "yieldmo.com",
    "sharethrough.com", "triplelift.com", "3lift.com", "indexww.com", "smartadserver.com",
    "teads.tv", "media.net", "adform.net", "flashtalking.com", "serving-sys.com",
    "sizmek.com", "innovid.com", "gumgum.com", "kargo.com", "sonobi.com", "33across.com",
    "contextweb.com", "lijit.com", "sovrn.com", "bidswitch.net", "adroll.com", "2mdn.net",
)

CASINO_KEYWORDS = [
    "casino", "slots?", "blackjack", "roulette", "poker", "baccarat", "jackpot",
    "free spins?", "sportsbook", "bet ?now", "betting", "wager", "igaming", "gambl",
    "draftkings", "fanduel", "betmgm", "caesars", "bet365", "pointsbet", "betrivers",
    "fanatics ?sportsbook", "hard ?rock ?bet", "espn ?bet", "golden ?nugget", "borgata",
    "pokerstars", "unibet", "888", "betway", "stake\\.com", "chumba", "pulsz",
    "wow ?vegas", "mcluck", "high ?5 ?casino", "21\\+", "1-800-gambler",
]
CASINO_RE = re.compile(r"(" + "|".join(CASINO_KEYWORDS) + r")", re.I)
MEDIA_EXT = re.compile(r"\.(jpe?g|png|gif|webp|svg|mp4|webm)(\?|$)", re.I)
REDIRECT_PARAMS = ("adurl", "url", "u", "redirect", "dest", "destination", "clickurl", "r")


def is_ad_host(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return any(host == d or host.endswith("." + d) for d in AD_DOMAINS)


def unwrap_click(url: str) -> str:
    """Follow common ?adurl=/?url= redirect wrappers to the real landing page."""
    for _ in range(4):
        qs = parse_qs(urlparse(url).query)
        nxt = next((qs[p][0] for p in REDIRECT_PARAMS if p in qs and qs[p][0].startswith("http")), None)
        if not nxt:
            break
        url = unquote(nxt)
    return url


FRAME_JS = """
() => {
  const abs = u => { try { return new URL(u, location.href).href } catch { return null } };
  const media = [];
  document.querySelectorAll('img, video, source, [style*="background"]').forEach(el => {
    let src = el.currentSrc || el.src || '';
    if (!src) { const m = (el.getAttribute('style')||'').match(/url\\(["']?([^"')]+)/); if (m) src = m[1]; }
    const r = el.getBoundingClientRect();
    if (src && (r.width >= 50 && r.height >= 30 || el.tagName !== 'IMG'))
      media.push({src: abs(src), alt: el.alt || '', w: Math.round(r.width), h: Math.round(r.height)});
  });
  const links = [...document.querySelectorAll('a[href]')].map(a => abs(a.href)).filter(Boolean);
  return {media, links, text: (document.body ? document.body.innerText : '').slice(0, 500)};
}
"""


def crawl_page(page, url, scroll_steps=8):
    ad_responses = []

    def on_response(resp):
        try:
            if is_ad_host(resp.url) or (MEDIA_EXT.search(resp.url) and is_ad_host(resp.frame.url)):
                ad_responses.append(resp.url)
        except Exception:
            pass

    page.on("response", on_response)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except Exception as e:
        print(f"  ! load failed: {e}", file=sys.stderr)
        return [], ad_responses
    for _ in range(scroll_steps):
        page.mouse.wheel(0, 1200)
        page.wait_for_timeout(1500)
    page.wait_for_timeout(4000)

    ads = []
    for frame in page.frames:
        if frame == page.main_frame or not (is_ad_host(frame.url) or "safeframe" in frame.url or frame.url == "about:blank"):
            continue
        try:
            data = frame.evaluate(FRAME_JS)
        except Exception:
            continue
        if not data["media"] and not data["links"]:
            continue
        landing = [unwrap_click(l) for l in data["links"]]
        blob = " ".join([data["text"], *landing, *(m["src"] for m in data["media"]), *(m["alt"] for m in data["media"])])
        hits = sorted({h.lower() for h in CASINO_RE.findall(blob)})
        el = None
        try:
            el = frame.frame_element()
        except Exception:
            pass
        ads.append({
            "publisher": url, "frame_url": frame.url, "landing_urls": sorted(set(landing)),
            "creatives": data["media"], "text": data["text"].strip(), "casino_matches": hits,
            "_element": el,
        })
    page.remove_listener("response", on_response)
    return ads, ad_responses


def download(url, dest_dir: Path, session):
    try:
        r = session.get(url, timeout=20)
        r.raise_for_status()
    except Exception as e:
        return None, str(e)
    ext = (MEDIA_EXT.search(url).group(1) if MEDIA_EXT.search(url) else
           (r.headers.get("content-type", "bin").split("/")[-1].split(";")[0]))
    path = dest_dir / f"{hashlib.sha1(r.content).hexdigest()[:16]}.{ext}"
    if not path.exists():
        path.write_bytes(r.content)
    return path, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--urls", nargs="*", help="publisher URLs (default: publishers.txt)")
    ap.add_argument("--out", default="output", help="output directory")
    ap.add_argument("--all", action="store_true", help="keep non-casino ads too")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    urls = args.urls or [l.strip() for l in (HERE / "publishers.txt").read_text().splitlines()
                         if l.strip() and not l.startswith("#")]
    run = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out) / run
    (out / "creatives").mkdir(parents=True, exist_ok=True)
    (out / "screenshots").mkdir(exist_ok=True)

    sess = requests.Session()
    sess.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36"
    kept, network_log = [], {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed, executable_path=os.environ.get("CHROMIUM_PATH") or None)
        ctx = browser.new_context(user_agent=sess.headers["User-Agent"], viewport={"width": 1366, "height": 900}, locale="en-US")
        for url in urls:
            print(f"-> {url}")
            page = ctx.new_page()
            ads, net = crawl_page(page, url)
            network_log[url] = net
            for ad in ads:
                if not (args.all or ad["casino_matches"]):
                    continue
                el = ad.pop("_element")
                if el:
                    shot = out / "screenshots" / f"{len(kept):04d}.png"
                    try:
                        el.screenshot(path=str(shot), timeout=5000); ad["screenshot"] = str(shot)
                    except Exception:
                        pass
                for c in ad["creatives"]:
                    if c["src"] and c["src"].startswith("http"):
                        path, err = download(c["src"], out / "creatives", sess)
                        c["file"] = str(path) if path else None
                        if err: c["error"] = err
                kept.append(ad)
                print(f"   + ad {ad['casino_matches'] or ''} -> {ad['landing_urls'][:1]}")
            for ad in ads: ad.pop("_element", None)
            page.close()
            time.sleep(1)
        browser.close()

    (out / "ads.json").write_text(json.dumps(kept, indent=2))
    (out / "ad_network_requests.json").write_text(json.dumps(network_log, indent=2))
    with open(out / "ads.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["publisher", "landing_url", "creative_url", "creative_file", "casino_matches", "screenshot"])
        for ad in kept:
            for c in ad["creatives"] or [{}]:
                w.writerow([ad["publisher"], " | ".join(ad["landing_urls"]), c.get("src", ""),
                            c.get("file", ""), ",".join(ad["casino_matches"]), ad.get("screenshot", "")])
    print(f"\nDone: {len(kept)} ads saved to {out}")


if __name__ == "__main__":
    main()
