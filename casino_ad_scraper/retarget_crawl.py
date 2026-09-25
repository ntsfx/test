"""Get retargeted by SA casino brands, then browse publishers and capture their ads.

Step 1 (warm-up): visit each brand site in sa_brands.txt with a persistent browser profile,
scroll and open a second page so the brand's retargeting pixels set their cookies.
Step 2 (browse): start from each site in publishers_za.txt and follow random same-site links
for --hops pages, capturing ad creatives with scraper.crawl_page and keeping ads that
mention a brand name.

Usage:
  python casino_ad_scraper/retarget_crawl.py --headed
  python casino_ad_scraper/retarget_crawl.py --skip-warmup --hops 10   # reuse existing cookies
"""
import argparse, json, os, random, re, sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from playwright.sync_api import sync_playwright

import scraper

HERE = Path(__file__).parent
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36"


def load_brands():
    rows = [l.split("|") for l in (HERE / "sa_brands.txt").read_text().splitlines()
            if "|" in l and not l.startswith("#")]
    return [(n.strip(), d.strip()) for n, d in rows]


def brand_regex(brands):
    terms = {re.escape(n).replace(r"\ ", " ?") for n, _ in brands}
    terms |= {re.escape(d.split(".")[0]) for _, d in brands}
    return re.compile("(" + "|".join(sorted(terms, key=len, reverse=True)) + ")", re.I)


def warmup(ctx, brands):
    page = ctx.new_page()
    for domain in dict.fromkeys(d for _, d in brands):
        print(f"  warm-up {domain}")
        try:
            page.goto(f"https://{domain}", wait_until="domcontentloaded", timeout=30000)
            for _ in range(3):
                page.mouse.wheel(0, 900)
                page.wait_for_timeout(1500)
            key = domain.split(".")[0]
            links = [h for h in page.eval_on_selector_all("a[href]", "as => as.map(a => a.href)") if key in h]
            if links:
                page.goto(random.choice(links), wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3000)
        except Exception as e:
            print(f"    ! {e.__class__.__name__}", file=sys.stderr)
    page.close()


def random_link(page, host):
    try:
        links = page.eval_on_selector_all("a[href]", "as => as.map(a => a.href)")
    except Exception:
        return None
    links = [h.split("#")[0] for h in links
             if h.startswith("http") and (urlparse(h).hostname or "").removeprefix("www.") == host]
    return random.choice(links) if links else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--publishers", default="publishers_za.txt")
    ap.add_argument("--hops", type=int, default=5, help="random pages per publisher")
    ap.add_argument("--profile", default="browser_profile", help="persistent profile dir (keeps cookies)")
    ap.add_argument("--skip-warmup", action="store_true")
    ap.add_argument("--all", action="store_true", help="keep every ad, not just brand matches")
    ap.add_argument("--out", default="output")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    brands = load_brands()
    brand_re = brand_regex(brands)
    starts = [l.strip() for l in (HERE / args.publishers).read_text().splitlines()
              if l.strip() and not l.startswith("#")]
    random.shuffle(starts)
    out = Path(args.out) / f"retarget-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    (out / "creatives").mkdir(parents=True, exist_ok=True)
    (out / "screenshots").mkdir(exist_ok=True)
    sess = requests.Session()
    sess.headers["User-Agent"] = UA
    kept, net_log = [], {}

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            args.profile, headless=not args.headed, executable_path=os.environ.get("CHROMIUM_PATH") or None,
            user_agent=sess.headers["User-Agent"], viewport={"width": 1366, "height": 900},
            locale="en-ZA", timezone_id="Africa/Johannesburg")
        if not args.skip_warmup:
            print("Warm-up: visiting brand sites")
            warmup(ctx, brands)
        page = ctx.new_page()
        for n, start in enumerate(starts, 1):
            url, host = start, (urlparse(start).hostname or "").removeprefix("www.")
            for hop in range(args.hops):
                print(f"-> [{n}/{len(starts)} page {hop + 1}] {url}")
                ads, net = scraper.crawl_page(page, url)
                net_log[url] = net
                for ad in ads:
                    el = ad.pop("_element", None)
                    blob = " ".join([ad["text"], *ad["landing_urls"], *(c["src"] or "" for c in ad["creatives"]),
                                     *(c["alt"] for c in ad["creatives"])])
                    ad["brands"] = sorted({m.lower() for m in brand_re.findall(blob)})
                    if not (args.all or ad["brands"] or ad["casino_matches"]):
                        continue
                    if el:
                        shot = out / "screenshots" / f"{len(kept):04d}.png"
                        try:
                            el.screenshot(path=str(shot), timeout=5000)
                            ad["screenshot"] = str(shot)
                        except Exception:
                            pass
                    for c in ad["creatives"]:
                        if c["src"] and c["src"].startswith("http"):
                            path, err = scraper.download(c["src"], out / "creatives", sess)
                            c["file"] = str(path) if path else None
                    ad["page"] = url
                    kept.append(ad)
                    print(f"   + {ad['brands'] or ad['casino_matches']} -> {ad['landing_urls'][:1]}")
                url = random_link(page, host)
                if not url:
                    break
            (out / "ads.json").write_text(json.dumps(kept, indent=2))  # saved after every site
            (out / "ad_network_requests.json").write_text(json.dumps(net_log, indent=2))
        ctx.close()
    print(f"\nDone: {len(kept)} ads saved to {out}")


if __name__ == "__main__":
    main()
