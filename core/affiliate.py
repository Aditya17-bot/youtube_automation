"""Affiliate link construction and the static hub site.

Two constraints shape this module.

1. Amazon's Product Advertising API is only granted after the Associates
   account is approved and has made qualifying sales, so before approval there
   is no legitimate source of product titles, prices or images. Rather than
   invent them, links point at a category SEARCH on Amazon and the on-screen
   copy describes a category, not a specific item. When PA-API access arrives,
   `product_url` takes an ASIN and nothing else changes.

2. Links in a Shorts description convert poorly, so every video also gets a
   page on a static hub site (free on GitHub Pages). The hub doubles as the
   "live platform with original content" that the Associates application
   requires.

The associate tag lives in channel config; until it is set, links are plain
Amazon URLs and still work - they simply earn nothing.
"""
from __future__ import annotations

import html
import json
import urllib.parse
from pathlib import Path

from core.config import PATHS

DISCLOSURE = "As an Amazon Associate, I earn from qualifying purchases."

# Marketplace domains, so the same pipeline can target a different country.
DOMAINS = {
    "in": "www.amazon.in",
    "us": "www.amazon.com",
    "uk": "www.amazon.co.uk",
}


def _domain(marketplace: str) -> str:
    return DOMAINS.get(marketplace, DOMAINS["in"])


def search_url(query: str, *, tag: str | None = None, marketplace: str = "in") -> str:
    """Link to a category search. Used before PA-API access exists."""
    params = {"k": query}
    if tag:
        params["tag"] = tag
    return f"https://{_domain(marketplace)}/s?" + urllib.parse.urlencode(params)


def product_url(asin: str, *, tag: str | None = None, marketplace: str = "in") -> str:
    """Direct product link. Used once real ASINs are available."""
    base = f"https://{_domain(marketplace)}/dp/{asin}"
    return f"{base}?tag={tag}" if tag else base


def link_for(item: dict, channel: dict) -> str:
    aff = channel.get("affiliate", {})
    tag = aff.get("associate_tag") or None
    marketplace = aff.get("marketplace", "in")
    if item.get("asin"):
        return product_url(item["asin"], tag=tag, marketplace=marketplace)
    return search_url(item["search_query"], tag=tag, marketplace=marketplace)


def hub_dir() -> Path:
    d = PATHS.root / "hub"
    d.mkdir(parents=True, exist_ok=True)
    return d


PAGE_CSS = """
:root{--bg:#0E1116;--card:#161B22;--text:#E6EDF3;--muted:#8B949E;--accent:#4ADE80;--line:#232A33}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
 font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:720px;margin:0 auto;padding:48px 20px 80px}
h1{font-size:clamp(28px,5vw,40px);line-height:1.2;margin:0 0 8px}
.sub{color:var(--muted);margin:0 0 36px}
.item{background:var(--card);border:1px solid var(--line);border-radius:14px;
 padding:20px 22px;margin:0 0 14px}
.item h2{font-size:19px;margin:0 0 6px}
.item p{color:var(--muted);margin:0 0 14px}
a.btn{display:inline-block;background:var(--accent);color:#07120B;font-weight:700;
 text-decoration:none;padding:10px 18px;border-radius:9px}
a.btn:hover{filter:brightness(1.08)}
.disc{color:var(--muted);font-size:13px;border-top:1px solid var(--line);
 margin-top:36px;padding-top:18px}
a.home{color:var(--muted);text-decoration:none;font-size:14px}
"""


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{html.escape(title)}</title><style>{PAGE_CSS}</style></head>"
        f"<body><div class=\"wrap\">{body}</div></body></html>"
    )


def write_video_page(slug: str, script: dict, channel: dict) -> Path:
    """One page per video, listing that video's picks."""
    items = script.get("items", [])
    rows = []
    for item in items:
        rows.append(
            "<div class=\"item\">"
            f"<h2>{html.escape(item['name'])}</h2>"
            f"<p>{html.escape(item.get('why', ''))}</p>"
            f"<a class=\"btn\" href=\"{html.escape(link_for(item, channel))}\" "
            "rel=\"nofollow sponsored noopener\" target=\"_blank\">See it on Amazon</a>"
            "</div>"
        )

    body = (
        "<a class=\"home\" href=\"./index.html\">&larr; all videos</a>"
        f"<h1>{html.escape(script['title'])}</h1>"
        f"<p class=\"sub\">{html.escape(script.get('description', ''))}</p>"
        + "".join(rows)
        + f"<p class=\"disc\">{html.escape(DISCLOSURE)}</p>"
    )

    dest = hub_dir() / f"{slug}.html"
    dest.write_text(_page(script["title"], body), encoding="utf-8")
    _record(slug, script["title"])
    rebuild_index(channel)
    return dest


def _index_path() -> Path:
    return hub_dir() / "_index.json"


def _record(slug: str, title: str) -> None:
    path = _index_path()
    entries = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    entries = [e for e in entries if e["slug"] != slug]
    entries.insert(0, {"slug": slug, "title": title})
    path.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def rebuild_index(channel: dict) -> Path:
    path = _index_path()
    entries = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    rows = "".join(
        "<div class=\"item\">"
        f"<h2><a class=\"home\" href=\"./{html.escape(e['slug'])}.html\" "
        f"style=\"color:var(--text);font-size:19px\">{html.escape(e['title'])}</a></h2>"
        "</div>"
        for e in entries
    )
    body = (
        f"<h1>{html.escape(channel['name'])}</h1>"
        "<p class=\"sub\">Picks from every video, in one place.</p>"
        + (rows or "<p class=\"sub\">Nothing published yet.</p>")
        + f"<p class=\"disc\">{html.escape(DISCLOSURE)}</p>"
    )
    dest = hub_dir() / "index.html"
    dest.write_text(_page(channel["name"], body), encoding="utf-8")
    return dest


def description_block(slug: str, script: dict, channel: dict) -> str:
    """The affiliate section appended to a YouTube description."""
    aff = channel.get("affiliate", {})
    base = (aff.get("hub_url") or "").rstrip("/")
    lines: list[str] = []

    if base:
        lines += [f"All links: {base}/{slug}.html", ""]

    lines.append("IN THIS VIDEO")
    for item in script.get("items", []):
        lines.append(f"- {item['name']}: {link_for(item, channel)}")
    lines += ["", DISCLOSURE]
    return "\n".join(lines)


def static_block(channel: dict) -> str:
    """Affiliate section for channels whose picks are curated, not scripted.

    The finance and horror channels have no per-video product list - and should
    not, since neither script should be inventing product claims. Instead each
    channel config carries a hand-written `affiliate.picks` list that is
    appended to every description unchanged.
    """
    aff = channel.get("affiliate", {})
    picks = aff.get("picks") or []
    if not picks:
        return ""

    lines: list[str] = []
    base = (aff.get("hub_url") or "").rstrip("/")
    if base:
        lines += [f"Everything mentioned: {base}/index.html", ""]

    lines.append("THINGS WORTH OWNING")
    for item in picks:
        lines.append(f"- {item['name']}: {link_for(item, channel)}")
        if item.get("why"):
            lines.append(f"  {item['why']}")
    lines += ["", DISCLOSURE]
    return "\n".join(lines)


def write_channel_page(channel: dict) -> Path:
    """A single hub page for a channel's curated picks."""
    picks = channel.get("affiliate", {}).get("picks") or []
    rows = "".join(
        "<div class=\"item\">"
        f"<h2>{html.escape(p['name'])}</h2>"
        f"<p>{html.escape(p.get('why', ''))}</p>"
        f"<a class=\"btn\" href=\"{html.escape(link_for(p, channel))}\" "
        "rel=\"nofollow sponsored noopener\" target=\"_blank\">See it on Amazon</a>"
        "</div>"
        for p in picks
    )
    body = (
        f"<h1>{html.escape(channel['name'])}</h1>"
        "<p class=\"sub\">Things mentioned on the channel.</p>"
        + (rows or "<p class=\"sub\">Nothing listed yet.</p>")
        + f"<p class=\"disc\">{html.escape(DISCLOSURE)}</p>"
    )
    dest = hub_dir() / f"{channel['_slug']}.html"
    dest.write_text(_page(channel["name"], body), encoding="utf-8")
    return dest
