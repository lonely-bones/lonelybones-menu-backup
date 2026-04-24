# Lonely Bones — Weedmaps Backup Menu

A static, self-hosted backup of the Lonely Bones dispensary menu on
Weedmaps. When Weedmaps goes down, customers are directed here so they
can still browse products. Rebuilt automatically once a day from the
Weedmaps public API.

## What it does

- Hits the public Weedmaps API for dispensary `lonely-bones`, pages
  through the entire menu (~263 products).
- Downloads, resizes and caches every product image + the dispensary logo.
- Renders a landing page + a page per product category:
  `index.html`, `flower.html`, `pre-rolls.html`, `concentrates.html`, `edibles.html`.
- Commits updated files back to this repo. GitHub Pages serves them as a
  live website.

## Repository layout

```
scrape.py                    # the scraper / page builder
requirements.txt             # Python deps (Pillow)
.github/workflows/daily.yml  # GitHub Actions schedule
index.html                   # landing page (category teasers)
flower.html  pre-rolls.html  concentrates.html  edibles.html
images/                      # cached product photos + logo
menu.json                    # last-known-good menu data
last_updated.txt             # timestamp of last successful run
```

## Running locally

```bash
pip install -r requirements.txt
python scrape.py
# open index.html in your browser
```

## How the automation works

`.github/workflows/daily.yml` runs `scrape.py` every day at 08:00 UTC.
If the menu has changed, the bot commits the updated HTML / images /
`menu.json` back to the repo, and GitHub Pages automatically redeploys.
You can also trigger a run manually from the "Actions" tab.

## Tweaking

- **Change the schedule:** edit the `cron:` line in `daily.yml`.
- **Change copy / colors / layout:** edit `scrape.py` (the `CSS`,
  `INFO_HTML`, and `render_card` / `render_pages` functions).
- **Use a different dispensary:** change `SLUG` at the top of `scrape.py`.
