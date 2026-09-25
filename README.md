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
