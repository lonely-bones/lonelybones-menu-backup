#!/usr/bin/env python3
"""
Lonely Bones menu backup scraper.

Hits the Weedmaps public API, pages through the full menu, downloads every
product image locally, and writes:
  - menu.json         : all product data (source of truth)
  - images/<id>.<ext> : cached product images
  - index.html        : landing page (category teasers, 4 per category)
  - flower.html, pre-rolls.html, concentrates.html, edibles.html, ... : full category pages
  - last_updated.txt  : human-readable timestamp

Runs on schedule via GitHub Actions (or any cron-capable host).
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SLUG = "lonely-bones"
API = f"https://api-g.weedmaps.com/discovery/v1/listings/dispensaries/{SLUG}/menu_items"
LISTING_API = f"https://api-g.weedmaps.com/discovery/v1/listings/dispensaries/{SLUG}"
PAGE_SIZE = 50
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

OUT_DIR = Path(__file__).parent
IMG_DIR = OUT_DIR / "images"
IMG_DIR.mkdir(exist_ok=True)

DISPLAY_NAMES = {
    "Concentrates": "Concentrates & Carts",
}
PAGE_SLUGS = {
    "Flower": "flower.html",
    "Pre-Rolls": "pre-rolls.html",
    "Concentrates": "concentrates.html",
    "Edibles": "edibles.html",
    "Vape": "vape.html",
    "Topicals": "topicals.html",
    "Tinctures": "tinctures.html",
    "Accessories": "accessories.html",
}
ORDER = ["Flower", "Pre-Rolls", "Concentrates", "Vape", "Edibles",
         "Topicals", "Tinctures", "Accessories"]


# ---------- HTTP ----------

def http_get(url: str, *, timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_page(page: int) -> dict:
    qs = urllib.parse.urlencode({"page": page, "page_size": PAGE_SIZE})
    data = http_get(f"{API}?{qs}")
    return json.loads(data)


def fetch_listing_info() -> dict:
    """Fetch the dispensary listing itself (for logo, hours, etc.)."""
    try:
        data = json.loads(http_get(LISTING_API))
        return data.get("data", {}).get("listing", {}) or {}
    except Exception as e:
        print(f"  ! listing fetch failed: {e}", flush=True)
        return {}


def fetch_and_save_logo() -> str | None:
    """Download the dispensary logo to images/logo.<ext>. Return local filename or None."""
    info = fetch_listing_info()
    url = (info.get("avatar_image") or {}).get("original_url") or (
        info.get("avatar_image") or {}
    ).get("small_url")
    if not url:
        return None
    return download_image(url, "logo")


def fetch_all_products() -> tuple[list[dict], dict]:
    all_items: list[dict] = []
    first_meta: dict = {}
    page = 1
    while True:
        print(f"  fetching page {page} ...", flush=True)
        payload = fetch_page(page)
        if page == 1:
            first_meta = payload.get("meta", {})
        items = payload.get("data", {}).get("menu_items", []) or []
        if not items:
            break
        all_items.extend(items)
        total = first_meta.get("total_menu_items", 0)
        if total and len(all_items) >= total:
            break
        if len(items) < PAGE_SIZE:
            break
        page += 1
        time.sleep(0.4)
        if page > 50:
            break
    return all_items, first_meta


def slugify_ext(url: str) -> str:
    path = urllib.parse.urlparse(url).path
    ext = os.path.splitext(path)[1].lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        ext = ".jpg"
    return ext


MAX_IMG_DIM = 800  # resize so longest side is this many pixels
JPEG_QUALITY = 82


def _compress_image(raw: bytes, want_jpeg: bool = True) -> tuple[bytes, str]:
    """Resize to MAX_IMG_DIM and re-encode. Returns (bytes, extension)."""
    try:
        from PIL import Image
        import io as _io
        img = Image.open(_io.BytesIO(raw))
        # Preserve PNG transparency for logos etc.
        if img.mode in ("RGBA", "LA", "P") and not want_jpeg:
            img.thumbnail((MAX_IMG_DIM, MAX_IMG_DIM))
            buf = _io.BytesIO()
            img.save(buf, format="PNG", optimize=True)
            return buf.getvalue(), ".png"
        img = img.convert("RGB")
        img.thumbnail((MAX_IMG_DIM, MAX_IMG_DIM))
        buf = _io.BytesIO()
        img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True,
                 progressive=True)
        return buf.getvalue(), ".jpg"
    except Exception as e:
        print(f"  ! compression skipped: {e}", flush=True)
        return raw, ""  # fall back to original


def download_image(url: str, product_id: int | str) -> str | None:
    if not url:
        return None
    # Check if we've already got a compressed version cached.
    for ext in (".jpg", ".png", ".jpeg", ".webp"):
        existing = IMG_DIR / f"{product_id}{ext}"
        if existing.exists() and existing.stat().st_size > 0:
            return existing.name
    try:
        raw = http_get(url, timeout=20)
        compressed, new_ext = _compress_image(raw, want_jpeg=(str(product_id) != "logo"))
        ext = new_ext or slugify_ext(url)
        local = IMG_DIR / f"{product_id}{ext}"
        local.write_bytes(compressed)
        return local.name
    except Exception as e:
        print(f"  ! image fail for {product_id}: {e}", flush=True)
        return None


# ---------- Normalize ----------

def normalize(raw: list[dict]) -> list[dict]:
    _merge = {
        "Pre-rolls": "Pre-Rolls",
        "Joints": "Pre-Rolls",
        "Concentrate": "Concentrates",
        "Edible": "Edibles",
        "Vaporizer": "Vape",
        "Topical": "Topicals",
        "Tincture": "Tinctures",
    }
    clean: list[dict] = []
    for p in raw:
        pid = p.get("id")
        img_url = (p.get("avatar_image") or {}).get("large_url") or (
            p.get("avatar_image") or {}
        ).get("original_url")
        local_img = download_image(img_url, pid) if pid else None
        prices = []
        pricing = p.get("prices") or {}
        for _unit, tiers in pricing.items():
            if isinstance(tiers, list):
                for t in tiers:
                    prices.append({
                        "label": t.get("label"), "price": t.get("price"),
                        "on_sale": t.get("on_sale"),
                        "original_price": t.get("original_price"),
                    })
            elif isinstance(tiers, dict):
                prices.append({
                    "label": tiers.get("label"), "price": tiers.get("price"),
                    "on_sale": tiers.get("on_sale"),
                    "original_price": tiers.get("original_price"),
                })
        genetics = (p.get("genetics_tag") or {}).get("name")
        category = (p.get("category") or {}).get("name")
        edge = p.get("edge_category") or {}
        edge_cat = edge.get("name")
        ancestors = edge.get("ancestors") or []
        top = ancestors[0].get("name") if ancestors else edge_cat
        top_category = _merge.get(top, top) if top else None
        thc = (p.get("metrics", {}).get("aggregates", {}) or {}).get("thc")
        clean.append({
            "id": pid, "name": p.get("name"),
            "category": category, "edge_category": edge_cat,
            "top_category": top_category,
            "genetics": genetics, "thc_pct": thc,
            "rating": p.get("rating"),
            "reviews_count": p.get("reviews_count"),
            "image_remote": img_url, "image_local": local_img,
            "prices": prices,
            "current_deal_title": p.get("current_deal_title"),
            "slug": p.get("slug"),
        })
    return clean


# ---------- Rendering ----------

def _h(s):
    if s is None:
        return ""
    return (
        str(s).replace("&", "&amp;").replace("<", "&lt;")
              .replace(">", "&gt;").replace('"', "&quot;")
    )


CSS = """
:root { --bg:#0f0f0f; --fg:#f1ece4; --muted:#9a9388; --card:#1a1a1a;
  --line:#2a2a2a; --accent:#d4a84b; --indica:#8e63c4; --sativa:#d2843a; --hybrid:#59a078; }
*{box-sizing:border-box}
html,body{margin:0;padding:0;background:var(--bg);color:var(--fg);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;line-height:1.4}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
header.site{padding:28px 20px 16px;text-align:center;border-bottom:1px solid var(--line);
  display:flex;flex-direction:column;align-items:center;gap:12px}
header.site img.logo{width:110px;height:110px;object-fit:contain;
  border-radius:12px;background:#fff;padding:6px}
header.site h1{margin:0;font-size:30px;letter-spacing:2px;font-weight:800}
header.site p{margin:0;color:var(--muted);font-size:13px;text-transform:uppercase;letter-spacing:1.5px}
.info{max-width:960px;margin:20px auto;padding:20px 24px;
  border:1px solid var(--accent);background:var(--card);border-radius:6px;
  font-size:14px;line-height:1.6}
.info h3{margin:0 0 6px;color:var(--accent);text-transform:uppercase;
  letter-spacing:1.5px;font-size:16px;text-align:center}
.info .sub{color:var(--muted);font-size:13px;text-align:center;margin:0 0 14px}
.info ol{margin:0 0 12px;padding-left:22px}
.info li{margin:8px 0}
.info li strong{color:var(--fg)}
.info .hours{margin-top:12px;padding-top:12px;border-top:1px dashed var(--line);
  color:var(--muted);font-size:13px;text-align:center}
.info a{color:var(--accent);font-weight:600}
.back-link{max-width:1200px;margin:14px auto 0;padding:0 16px;}
.back-link a{font-size:14px;letter-spacing:.5px;text-transform:uppercase;
  color:var(--muted);font-weight:600}
.back-link a:hover{color:var(--accent)}
main{max-width:1200px;margin:0 auto;padding:8px 16px 48px}
.cat-header{display:flex;align-items:center;justify-content:space-between;
  flex-wrap:wrap;gap:12px;margin:36px 0 14px;
  border-bottom:2px solid var(--accent);padding-bottom:10px}
.cat-header h2{font-size:26px;margin:0;letter-spacing:1px;
  text-transform:uppercase;color:var(--accent)}
.cat-header .section-count{color:var(--fg);font-weight:700;font-size:14px;
  text-transform:none;letter-spacing:normal;margin-left:10px}
.see-all{background:var(--accent);color:#1a1a1a;border:none;
  padding:10px 16px;border-radius:4px;font-size:14px;font-weight:700;
  text-transform:uppercase;letter-spacing:.5px;cursor:pointer;
  text-decoration:none;display:inline-block}
.see-all:hover{background:#e8bd5a;text-decoration:none}
h1.full-cat{font-size:32px;margin:24px 0 16px;text-transform:uppercase;
  letter-spacing:1.5px;color:var(--accent);border-bottom:2px solid var(--accent);
  padding-bottom:12px}
h1.full-cat .section-count{color:var(--fg);font-weight:700;font-size:16px;
  text-transform:none;letter-spacing:normal;margin-left:12px}
.grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fill,minmax(220px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;
  overflow:hidden;display:flex;flex-direction:column}
.img-wrap{aspect-ratio:1/1;background:#111;overflow:hidden;
  display:flex;align-items:center;justify-content:center}
.img-wrap img{width:100%;height:100%;object-fit:cover}
.body{padding:10px 12px 12px;display:flex;flex-direction:column;gap:6px}
.tags{display:flex;gap:6px;flex-wrap:wrap;font-size:11px}
.g{padding:2px 6px;border-radius:3px;text-transform:uppercase;letter-spacing:.5px;font-weight:600}
.g-indica{background:var(--indica);color:#fff}
.g-sativa{background:var(--sativa);color:#fff}
.g-hybrid{background:var(--hybrid);color:#fff}
.thc{padding:2px 6px;border:1px solid var(--line);color:var(--muted);border-radius:3px}
.name{font-size:14px;margin:2px 0;font-weight:600;line-height:1.3}
.deal{font-size:11px;color:var(--accent);text-transform:uppercase;letter-spacing:.5px}
ul.prices{list-style:none;margin:4px 0 0;padding:0;font-size:13px}
ul.prices li{display:flex;justify-content:space-between;padding:2px 0;border-top:1px dashed var(--line)}
ul.prices li:first-child{border-top:none}
.qty{color:var(--muted)}
.price{font-weight:600}
.sale{color:var(--accent)}
.strike{color:var(--muted);text-decoration:line-through;font-size:12px}
footer{text-align:center;color:var(--muted);font-size:12px;padding:24px}
@media (max-width:600px){
  .cat-header h2{font-size:20px}
  .see-all{font-size:12px;padding:8px 12px}
  h1.full-cat{font-size:22px}
}
@media (max-width:480px){
  .grid{grid-template-columns:repeat(auto-fill,minmax(150px,1fr))}
  .name{font-size:13px}
}
"""


def render_card(p: dict) -> str:
    remote = p.get("image_remote") or ""
    local = f"images/{p['image_local']}" if p.get("image_local") else ""
    img_src = remote or local
    img_fallback = (
        f' onerror="this.onerror=null;this.src=\'{local}\';"'
        if local else ''
    )
    genetics = p.get("genetics") or ""
    g_cls = genetics.lower()
    thc = p.get("thc_pct")
    thc_html = f'<span class="thc">{thc:.1f}% THC</span>' if thc else ""
    deal = p.get("current_deal_title")
    deal_html = f'<div class="deal">{_h(deal)}</div>' if deal else ""
    prices = p.get("prices") or []
    seen = set()
    rows = []
    for pr in prices:
        k = (pr.get("label"), pr.get("price"))
        if k in seen or pr.get("price") is None:
            continue
        seen.add(k)
        price_str = f"${pr['price']:g}"
        if pr.get("on_sale") and pr.get("original_price") not in (None, pr.get("price")):
            price_str = (
                f'<span class="sale">${pr["price"]:g}</span> '
                f'<span class="strike">${pr["original_price"]:g}</span>'
            )
        rows.append(
            f'<li><span class="qty">{_h(pr.get("label") or "")}</span>'
            f'<span class="price">{price_str}</span></li>'
        )
    prices_html = "<ul class='prices'>" + "".join(rows) + "</ul>" if rows else ""
    return f"""<article class="card">
  <div class="img-wrap"><img loading="lazy" src="{_h(img_src)}"{img_fallback} alt="{_h(p.get('name') or '')}"></div>
  <div class="body">
    <div class="tags">
      {f'<span class="g g-{g_cls}">{_h(genetics)}</span>' if genetics else ''}
      {thc_html}
    </div>
    <h3 class="name">{_h(p.get('name') or '')}</h3>
    {deal_html}
    {prices_html}
  </div>
</article>"""


INFO_HTML = """<div class="info">
  <h3>Weedmaps is down &mdash; here&rsquo;s how to order</h3>
  <p class="sub">Only use this menu while Weedmaps is down. Once it&rsquo;s back up, return to our Weedmaps page to order.</p>
  <ol>
    <li><strong>Text <a href="sms:+12072053004">(207) 205-3004</a></strong>
      (preferred) or <a href="tel:+12072053004">call</a> to place your order
      from the menu below.</li>
    <li>Come to <strong>72 Emery St, Sanford, Maine</strong>. We&rsquo;re open
      <strong>9:30am &ndash; 6:30pm daily, except major holidays</strong>
      &mdash; please check we&rsquo;re open before traveling long distances.</li>
    <li><strong>Curbside pickup only &mdash; please stay in your vehicle.</strong>
      Text us the <strong>color and make of your car</strong> when you arrive
      and we&rsquo;ll bring your order out.</li>
    <li><strong>Cash only.</strong> Please let us know if you need change.</li>
  </ol>
</div>"""


def page_shell(title: str, body_html: str, total: int, updated: str,
               back: bool = False, logo_file: str | None = None) -> str:
    back_html = (
        '<div class="back-link"><a href="index.html">&larr; Back to main menu</a></div>'
        if back else ""
    )
    logo_html = (
        f'<img class="logo" src="images/{_h(logo_file)}" alt="Lonely Bones logo">'
        if logo_file else ""
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>{_h(title)}</title>
<style>{CSS}</style>
</head>
<body>
<header class="site">
  {logo_html}
  <h1>LONELY BONES</h1>
  <p>Menu &middot; backup view</p>
</header>
{INFO_HTML}
{back_html}
<main>
{body_html}
</main>
<footer>
  {total} products total &middot; last updated {updated}
</footer>
</body>
</html>
"""


def render_pages(products: list[dict], logo_file: str | None = None) -> dict[str, str]:
    """Return {filename: html} for landing + each category page."""
    updated = datetime.now(timezone.utc).strftime("%B %d, %Y at %I:%M %p UTC")
    total = len(products)

    by_cat: dict[str, list[dict]] = {}
    for p in products:
        key = p.get("top_category") or p.get("edge_category") or p.get("category") or "Other"
        by_cat.setdefault(key, []).append(p)
    ordered = [k for k in ORDER if k in by_cat] + sorted(
        k for k in by_cat if k not in ORDER
    )

    out: dict[str, str] = {}

    # ----- Landing page -----
    PREVIEW_COUNT = 4
    parts = []
    for key in ordered:
        items = by_cat[key]
        display = DISPLAY_NAMES.get(key, key)
        page_file = PAGE_SLUGS.get(key, f"{key.lower().replace(' ', '-')}.html")
        preview_items = items[:PREVIEW_COUNT]
        see_all_btn = (
            f'<a class="see-all" href="{_h(page_file)}">'
            f'CLICK HERE TO SEE ALL {_h(display.upper())} &rarr;</a>'
        )
        parts.append(
            f'<section class="cat-section">'
            f'<div class="cat-header">'
            f'<h2>{_h(display)}'
            f'<span class="section-count"><strong>{len(items)} total</strong></span></h2>'
            f'{see_all_btn}'
            f'</div>'
            f'<div class="grid">'
        )
        for p in preview_items:
            parts.append(render_card(p))
        parts.append("</div></section>")
    out["index.html"] = page_shell(
        "Lonely Bones — Menu (Backup)",
        "\n".join(parts), total, updated, back=False, logo_file=logo_file,
    )

    # ----- One full page per category -----
    for key in ordered:
        items = by_cat[key]
        display = DISPLAY_NAMES.get(key, key)
        page_file = PAGE_SLUGS.get(key, f"{key.lower().replace(' ', '-')}.html")
        body = [
            f'<h1 class="full-cat">{_h(display)}'
            f'<span class="section-count">{len(items)} total</span></h1>',
            '<div class="grid">',
        ]
        for p in items:
            body.append(render_card(p))
        body.append("</div>")
        out[page_file] = page_shell(
            f"{display} — Lonely Bones (Backup)",
            "\n".join(body), total, updated, back=True, logo_file=logo_file,
        )
    return out


def main():
    print("Fetching logo ...", flush=True)
    logo_file = fetch_and_save_logo()
    print(f"  logo -> {logo_file}", flush=True)
    print("Fetching menu from Weedmaps ...", flush=True)
    raw, meta = fetch_all_products()
    print(f"  got {len(raw)} raw products (api meta total={meta.get('total_menu_items')})", flush=True)
    print("Normalizing + downloading images ...", flush=True)
    products = normalize(raw)
    (OUT_DIR / "menu.json").write_text(json.dumps(
        {"meta": meta, "products": products, "logo_file": logo_file}, indent=2))

    pages = render_pages(products, logo_file=logo_file)
    for filename, html in pages.items():
        (OUT_DIR / filename).write_text(html)
    (OUT_DIR / "last_updated.txt").write_text(
        datetime.now(timezone.utc).isoformat() + "\n"
    )
    print(f"Wrote {len(pages)} pages ({', '.join(pages)})", flush=True)


if __name__ == "__main__":
    main()
