"""Turn the exported landing-page bundle into a static site that crawlers and link previews can read.

The design tool exports one self-unpacking HTML file: every asset is base64 inside it and the
real page only exists after JavaScript runs, so search engines and WhatsApp, X or Slack previews
see a page titled "Bundled Page" with no content. This script unpacks it into plain files and
adds the SEO and social tags.

    python3 site/build.py            # site/index.html -> _site/
"""

import base64
import datetime
import gzip
import html
import json
import re
import shutil
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
URL = "https://timurcatakli.github.io/thumbly/"
TITLE = "Thumbly: let AI agents see and control your iPhone"
DESCRIPTION = (
    "Thumbly is a free, open-source Mac app and MCP server that lets Claude, ChatGPT, Cursor "
    "and other AI agents see and tap your real iPhone. You approve every client and can pause "
    "AI control in one click."
)
OG_ALT = "Thumbly: an MCP server that lets AI agents see and tap your iPhone"
EXT = {
    "image/png": "png",
    "image/svg+xml": "svg",
    "video/mp4": "mp4",
    "font/woff2": "woff2",
    "font/ttf": "ttf",
    "text/javascript": "js",
    "text/html": "html",
}

# Single-colour versions of the logos that ship as coloured app tiles. The page draws client
# logos as a CSS mask, which keeps only the shape, so a logo on a solid square shows as a block.
MONO_ICONS = {
    "Codex": "codex.svg",
    "Gemini": "gemini.svg",
}


def block(src: str, kind: str) -> str:
    match = re.search(
        rf'<script type="__bundler/{kind}">\n(.*?)\n\s*</script>', src, re.DOTALL
    )
    if not match:
        sys.exit(
            f"build.py: {kind} block not found; is site/index.html a bundle export?"
        )
    return match.group(1)


def unpack(src: str, out: Path) -> str:
    """Write every asset to out/assets and return the page with references pointing at them."""
    manifest = json.loads(block(src, "manifest"))
    ext_resources = json.loads(block(src, "ext_resources"))
    page = json.loads(block(src, "template"))
    (out / "assets").mkdir(parents=True)

    runtime = re.search(r'<script src="([0-9a-f-]{36})"></script>', page)
    paths = {}
    for uuid, entry in manifest.items():
        data = base64.b64decode(entry["data"])
        if entry["compressed"]:
            data = gzip.decompress(data)
        # Nested pages load the runtime as ./support.js, so the runtime keeps that name.
        name = (
            "support.js"
            if runtime and uuid == runtime.group(1)
            else f"{uuid}.{EXT[entry['mime']]}"
        )
        (out / "assets" / name).write_bytes(data)
        paths[uuid] = f"assets/{name}"

    for uuid, path in paths.items():
        page = page.replace(uuid, path)
    # The unpacker drops these too: they would not match the rewritten URLs.
    page = re.sub(r'\s+(integrity|crossorigin)="[^"]*"', "", page)
    resources = {e["id"]: paths[e["uuid"]] for e in ext_resources if e["uuid"] in paths}
    resources_js = json.dumps(resources).replace("</", "<\\/")
    script = f"<script>window.__resources = {resources_js};</script>"
    return page.replace("<head>", "<head>\n" + script, 1)


def fix_icons(page: str) -> str:
    for name, file in MONO_ICONS.items():
        svg = (ROOT / "icons" / file).read_text().strip()
        icon = "data:image/svg+xml," + urllib.parse.quote(svg, safe="")
        page, count = re.subn(
            rf"(\{{ name: '{name}', icon: ')[^']+'",
            lambda m, icon=icon: m.group(1) + icon + "'",
            page,
        )
        if count != 1:
            sys.exit(f"build.py: expected one {name} client icon, found {count}")
    return page


# Copy changes made after the export; drop an entry once the design itself says the new text.
COPY = {
    "Thumbly. Your thumb, for AI.": "Thumbly. MCP for iPhone",
}


def fix_copy(page: str) -> str:
    for old, new in COPY.items():
        if old not in page and new not in page:
            sys.exit(f"build.py: copy {old!r} not found in the export")
        page = page.replace(old, new)
    return page


def head(version: str) -> str:
    e = html.escape
    data = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "SoftwareApplication",
                "name": "Thumbly",
                "description": DESCRIPTION,
                "url": URL,
                "image": URL + "og.png",
                "applicationCategory": "DeveloperApplication",
                "operatingSystem": "macOS 14 or later",
                "softwareVersion": version,
                "license": "https://www.apache.org/licenses/LICENSE-2.0",
                "downloadUrl": "https://github.com/timurcatakli/thumbly#install",
                "softwareHelp": "https://github.com/timurcatakli/thumbly/tree/main/docs",
                "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
                "author": {
                    "@type": "Person",
                    "name": "Timur Catakli",
                    "url": "https://github.com/timurcatakli",
                },
            },
            {
                "@type": "SoftwareSourceCode",
                "name": "Thumbly",
                "codeRepository": "https://github.com/timurcatakli/thumbly",
                "programmingLanguage": ["Python", "Swift"],
                "runtimePlatform": "macOS",
            },
            {"@type": "WebSite", "name": "Thumbly", "url": URL},
        ],
    }
    return f"""
<title>{e(TITLE)}</title>
<meta name="description" content="{e(DESCRIPTION)}">
<meta name="robots" content="index, follow, max-image-preview:large">
<link rel="canonical" href="{URL}">
<meta name="author" content="Timur Catakli">
<meta name="google-site-verification" content="8S3_WuYqsjjlJGmjs8J19q4N4MySBwqH0-Bgri5sziY">
<meta name="theme-color" content="#ec3013">
<meta name="color-scheme" content="light dark">
<link rel="icon" href="favicon.ico" sizes="32x32">
<link rel="icon" href="icon.png" type="image/png" sizes="512x512">
<link rel="apple-touch-icon" href="apple-touch-icon.png">
<link rel="manifest" href="site.webmanifest">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Thumbly">
<meta property="og:locale" content="en_US">
<meta property="og:url" content="{URL}">
<meta property="og:title" content="{e(TITLE)}">
<meta property="og:description" content="{e(DESCRIPTION)}">
<meta property="og:image" content="{URL}og.png">
<meta property="og:image:secure_url" content="{URL}og.png">
<meta property="og:image:type" content="image/png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="{e(OG_ALT)}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:creator" content="@timurcatakli">
<meta name="twitter:title" content="{e(TITLE)}">
<meta name="twitter:description" content="{e(DESCRIPTION)}">
<meta name="twitter:image" content="{URL}og.png">
<meta name="twitter:image:alt" content="{e(OG_ALT)}">
<script type="application/ld+json">{json.dumps(data, separators=(",", ":"))}</script>"""


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "_site"
    shutil.rmtree(out, ignore_errors=True)
    version = (REPO / "VERSION").read_text().strip()

    page = fix_copy(fix_icons(unpack((ROOT / "index.html").read_text(), out)))
    page = re.sub(r"<html[^>]*>", '<html lang="en">', page, count=1)
    page = re.sub(r"<title>.*?</title>\s*", "", page, flags=re.DOTALL)
    page = re.sub(
        r'(<meta name="viewport"[^>]*>)',
        lambda m: m.group(1) + head(version),
        page,
        count=1,
    )
    (out / "index.html").write_text(page)

    for file in (
        "og.png",
        "icon.png",
        "apple-touch-icon.png",
        "favicon.ico",
        "site.webmanifest",
    ):
        shutil.copy(ROOT / "public" / file, out / file)
    # No robots.txt: crawlers only read it at the domain root, not under /thumbly/. Submit the
    # sitemap in Google Search Console instead.
    today = datetime.date.today().isoformat()
    (out / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"  <url><loc>{URL}</loc><lastmod>{today}</lastmod></url>\n"
        "</urlset>\n"
    )
    (out / ".nojekyll").write_text("")
    print(
        f"Built {out} ({sum(f.stat().st_size for f in out.rglob('*') if f.is_file()) // 1024} KB)"
    )


if __name__ == "__main__":
    main()
