# Casino ad scraper

Crawls publisher sites in headless Chromium, captures programmatic ad creatives
(served via DoubleClick, AppNexus/Xandr, Rubicon, PubMatic, Criteo, etc.), keeps the
ones that match casino/sportsbook keywords, and saves:

- `ads.json` / `ads.csv`: publisher, landing URL(s) with redirect wrappers removed, creative URL, local file, matched keywords
- `creatives/`: downloaded image/video creatives, named by content hash so duplicates are skipped
- `screenshots/`: a rendered screenshot of each ad slot, which also captures HTML5/canvas ads
- `ad_network_requests.json`: raw ad-tech requests seen on each page

```bash
pip install -r requirements.txt
python -m playwright install chromium   # or set CHROMIUM_PATH=/path/to/chromium
python casino_ad_scraper/scraper.py                       # crawl publishers.txt
python casino_ad_scraper/scraper.py --urls https://www.covers.com --all --headed
```

Edit `casino_ad_scraper/publishers.txt` for target sites and `CASINO_KEYWORDS` / `AD_DOMAINS` in `scraper.py` to tune detection.

Notes: ads are geo-targeted and personalized, so run from the market you care about (e.g. a US proxy/VPN in a legal-betting state). Respect the sites' terms of service and keep crawl rates low.

## South Africa

- `casino_ad_scraper/sa_brands.txt`: 51 SA casino/betting brands (name | domain).
- `casino_ad_scraper/publishers_za.txt`: 296 SA and SA-popular sites to browse.

### 1. Ad libraries (every live ad per brand, no bot needed)

```bash
python casino_ad_scraper/ad_libraries.py                 # all brands, Google + Meta, region ZA
python casino_ad_scraper/ad_libraries.py --source google --brands Betway Hollywoodbets --headed
```

### 2. Retargeting crawl

Visits every brand site first so their retargeting pixels tag the browser profile, then browses
random pages on each publisher and saves brand/casino ads it's served.

```bash
python casino_ad_scraper/retarget_crawl.py --headed                  # warm-up + browse (5 pages per site)
python casino_ad_scraper/retarget_crawl.py --skip-warmup --hops 10   # reuse the saved cookies
```

Cookies are kept in `browser_profile/`. Run from a South African IP. Retargeting audiences can take
a few hours to pick up a new visitor, so a second run with `--skip-warmup` later often finds more.
