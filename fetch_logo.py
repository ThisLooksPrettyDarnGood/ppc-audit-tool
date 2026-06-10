"""
fetch_logo.py
-------------
Download a crisp client logo from their website and return raw bytes + content type.

The job is a SHARP brand logo for slide 1, not just "any image". A 32px favicon scaled
up to fill the logo box looks blurry (Dan scored that 30/100), so we:
  • read each candidate's real pixel size from its header (no extra dependency),
  • reject anything too small to look crisp, keeping the largest seen as a fallback, and
  • rasterise SVG logos (the only real logo on many modern sites) to a crisp PNG, recolouring
    pure-white logos to dark so they show on the white logo box.

Candidate priority (best brand-logo signal first):
  TRUSTED on-page (the site's OWN clean logo, any aspect ratio):
    1. First <img> inside <header> / <nav>      — most reliable spot for the site's own logo
    2. <img> whose src/alt says "logo" AND carries the site's brand token
    3. apple-touch-icon                          — clean square, usually 180px+
    4. Largest sized <link rel="icon">
  Then Google favicon service (sz=256)           — always the site's own icon, up to 256px
  LOW-PRIORITY (only if nothing better):
    5. other "logo"-named imgs                   — may be third-party partner logos
    6. og:image                                  — often a wide social *banner*, not a logo
    7. First few <img> in document order

SVG logos are rasterised with PyMuPDF (a self-contained wheel - no system libraries - so it
works on Streamlit Cloud from requirements.txt alone). If PyMuPDF is missing it degrades
gracefully to the raster path. (Clearbit's public logo API was sunset by HubSpot, so unused.)

Returns (bytes, content_type) or (None, None) if nothing usable found.
"""

import re
import struct
import requests
from urllib.parse import urlparse, urljoin

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PPCAuditTool/1.0)"}
TIMEOUT = 8
MAX_LOGO_BYTES = 400_000   # raised from 150k: clean PNG logos with transparency can be big
MIN_LOGO_PX = 90           # smallest side below this looks blurry scaled into the logo box

# Google Slides' replaceImage only accepts PNG / JPEG / GIF. SVG and ICO are fetched
# fine but FAIL silently at insertion - so we must reject them here.
_SLIDES_OK = ("image/png", "image/jpeg", "image/jpg", "image/gif")


def _parse_root(website_url: str) -> str | None:
    url = website_url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else None


def _image_dims(data: bytes) -> tuple[int, int] | None:
    """Best-effort (width, height) straight from the image header - PNG, GIF, JPEG.
    No Pillow dependency. Returns None if it can't be read (we then treat size as unknown)."""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
            w, h = struct.unpack(">II", data[16:24])
            return int(w), int(h)
        if data[:6] in (b"GIF87a", b"GIF89a"):
            w, h = struct.unpack("<HH", data[6:10])
            return int(w), int(h)
        if data[:2] == b"\xff\xd8":   # JPEG: scan for a Start-Of-Frame marker
            i, n = 2, len(data)
            while i + 9 < n:
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return int(w), int(h)
                seg_len = struct.unpack(">H", data[i + 2:i + 4])[0]
                i += 2 + seg_len
    except Exception:
        pass
    return None


def _download(url: str) -> tuple[bytes | None, str | None, int]:
    """Download an image Slides can use (PNG/JPEG/GIF) under the size cap.
    Returns (bytes, content_type, min_side_px). min_side_px is 0 when unknown."""
    try:
        try:
            head = requests.head(url, timeout=TIMEOUT, headers=HEADERS, allow_redirects=True)
            size = int(head.headers.get("Content-Length", 0) or 0)
            if size and size > MAX_LOGO_BYTES:
                return None, None, 0
        except Exception:
            pass

        r = requests.get(url, timeout=TIMEOUT, headers=HEADERS, allow_redirects=True)
        if r.status_code != 200:
            return None, None, 0
        ct = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
        path = url.lower().split("?")[0]
        if "svg" in ct or path.endswith((".svg", ".ico")):
            return None, None, 0
        is_raster = ct in _SLIDES_OK
        if not ct and path.endswith((".png", ".jpg", ".jpeg", ".gif")):
            is_raster, ct = True, "image/png"
        if is_raster and 100 < len(r.content) <= MAX_LOGO_BYTES:
            dims = _image_dims(r.content)
            min_side = min(dims) if dims else 0
            return r.content, ct or "image/png", min_side
    except Exception:
        pass
    return None, None, 0


_SVG_TARGET_W = 600        # render width; vector stays crisp at any size
_SVG_MAX_SIDE = 2000       # clamp so a pathological viewBox can't explode the render
_WHITE_FILLS = {"white", "#fff", "#ffffff"}


def _recolour_white_svg(svg: str) -> str:
    """Many brand logos are pure-white SVGs built for a dark site header (IB Masters is one).
    On slide 1's white logo box a white logo is invisible, so if EVERY explicit fill is white
    we recolour them to a dark neutral. Mixed/colour logos are left untouched."""
    fills = re.findall(r'fill\s*=\s*["\']([^"\']+)["\']', svg, re.IGNORECASE)
    explicit = [f.strip().lower() for f in fills if f.strip().lower() not in ("none", "")]
    if explicit and all(f in _WHITE_FILLS for f in explicit):
        svg = re.sub(r'(fill\s*=\s*["\'])(white|#fff|#ffffff)(["\'])',
                     r'\g<1>#1a1a1a\g<3>', svg, flags=re.IGNORECASE)
    return svg


def _rasterise_svg(svg_bytes: bytes) -> bytes | None:
    """Rasterise an SVG logo to a crisp PNG via PyMuPDF (self-contained wheel, no system
    libs - so it works on Streamlit Cloud from requirements.txt alone). Returns PNG bytes
    on a white background, or None if PyMuPDF is unavailable or rendering fails. Wrapped so a
    missing dependency degrades gracefully to the raster-logo path."""
    try:
        import fitz  # PyMuPDF
        svg = svg_bytes.decode("utf-8", "ignore")
        svg = _recolour_white_svg(svg)
        doc = fitz.open(stream=svg.encode("utf-8"), filetype="svg")
        page = doc[0]
        w = page.rect.width or _SVG_TARGET_W
        zoom = max(1.0, min(_SVG_TARGET_W / w, _SVG_MAX_SIDE / max(page.rect.height, 1)))
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        png = pix.tobytes("png")
        return png if 100 < len(png) <= MAX_LOGO_BYTES else None
    except Exception:
        return None


def _try_svg(url: str) -> tuple[bytes | None, str | None]:
    """Fetch an SVG candidate and rasterise it. Returns (png_bytes, 'image/png') or (None, None)."""
    try:
        r = requests.get(url, timeout=TIMEOUT, headers=HEADERS, allow_redirects=True)
        ct = r.headers.get("Content-Type", "").lower()
        if r.status_code == 200 and ("svg" in ct or url.lower().split("?")[0].endswith(".svg")):
            png = _rasterise_svg(r.content)
            if png:
                return png, "image/png"
    except Exception:
        pass
    return None, None


def fetch_logo_bytes(website_url: str) -> tuple[bytes, str] | tuple[None, None]:
    """Returns (image_bytes, content_type) or (None, None)."""
    root = _parse_root(website_url)
    if not root:
        return None, None

    html = ""
    try:
        r = requests.get(root, timeout=TIMEOUT, headers=HEADERS, allow_redirects=True)
        if r.status_code == 200:
            html = r.text
    except Exception:
        pass

    domain = urlparse(root).netloc
    # Brand token from the domain (e.g. 'ibmasters', 'notion') - used to tell the site's OWN
    # logo apart from third-party logos embedded on the page.
    brand_token = domain.lower()
    if brand_token.startswith("www."):
        brand_token = brand_token[4:]
    brand_token = brand_token.split(".")[0]

    trusted: list[str] = []     # clean OWN-brand logos, any aspect ratio
    logo_other: list[str] = []  # 'logo'-named imgs that aren't clearly the site's own brand
    loose: list[str] = []       # og:image banners and stray imgs - only if nothing better

    if html:
        # 1. First <img> inside <header> or <nav> - the most reliable spot for a site's OWN
        #    logo. (A marketing page's "trusted by" partner logos live lower in the body.)
        for container_tag in ("header", "nav"):
            m = re.search(
                rf'<{container_tag}[^>]*>(.*?)</{container_tag}>',
                html, re.IGNORECASE | re.DOTALL,
            )
            if m:
                img = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', m.group(1), re.IGNORECASE)
                if img:
                    trusted.append(urljoin(root, img.group(1)))
                    break

        # 2. <img> whose src/alt contains "logo". Split by brand: one carrying the site's own
        #    brand token is trusted; the rest (often third-party partner logos, e.g. a Vercel /
        #    Figma "customers" strip) drop to a low-priority tier so they can never beat the
        #    site's own clean icon.
        logo_imgs = re.findall(
            r'<img[^>]+(?:src=["\']([^"\']*logo[^"\']*)["\']|alt=["\'][^"\']*logo[^"\']*["\'][^>]+src=["\']([^"\']+)["\'])',
            html, re.IGNORECASE,
        )
        for groups in logo_imgs[:5]:
            img_url = groups[0] or groups[1]
            if not img_url:
                continue
            full = urljoin(root, img_url)
            if len(brand_token) >= 3 and brand_token in full.lower():
                trusted.append(full)
            else:
                logo_other.append(full)

        # 3. Apple touch icon - clean square, usually 180px+
        m = re.search(
            r'<link[^>]+rel=["\']apple-touch-icon["\'][^>]+href=["\']([^"\']+)["\']',
            html, re.IGNORECASE,
        )
        if not m:
            m = re.search(
                r'<link[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\']apple-touch-icon["\']',
                html, re.IGNORECASE,
            )
        if m:
            trusted.append(urljoin(root, m.group(1)))

        # 4. Largest sized <link rel="icon">
        icons = re.findall(
            r'<link[^>]+rel=["\'][^"\']*icon[^"\']*["\'][^>]*>',
            html, re.IGNORECASE,
        )
        sized_icons = []
        for tag in icons:
            href = re.search(r'href=["\']([^"\']+)["\']', tag)
            size = re.search(r'sizes=["\'](\d+)x\d+["\']', tag)
            if href:
                sized_icons.append((int(size.group(1)) if size else 0, urljoin(root, href.group(1))))
        for _, icon_url in sorted(sized_icons, key=lambda x: x[0], reverse=True):
            trusted.append(icon_url)

        # 5. og:image - often a wide social banner, not a logo, so it ranks below Clearbit
        m = re.search(
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            html, re.IGNORECASE,
        )
        if not m:
            m = re.search(
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
                html, re.IGNORECASE,
            )
        if m:
            loose.append(urljoin(root, m.group(1)))

        # 6. First few <img> tags in document order
        all_imgs = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE)
        for img_url in all_imgs[:5]:
            loose.append(urljoin(root, img_url))

    # Google's favicon service serves the site's best square icon (up to 256px) and always
    # resolves - a cleaner logo-box fill than an og:image banner, so it sits above the loose set.
    google_fav = f"https://www.google.com/s2/favicons?domain={domain}&sz=256"
    # Own-brand candidates first, then the always-own-brand favicon, then the riskier
    # 'logo'-named third-party imgs, then banners/stray imgs.
    ordered = trusted + [google_fav] + logo_other + loose

    # Take the first candidate that's crisp enough; if none clear the bar, keep the largest
    # seen so we never fail outright on a logo-only site.
    seen: set[str] = set()
    best: tuple[bytes, str, int] | None = None
    for url in ordered:
        if url in seen:
            continue
        seen.add(url)
        # SVG candidate (often the only true brand logo): rasterise it. A vector render is
        # always crisp, so a success wins immediately - this is what gets the real wordmark
        # onto slide 1 instead of falling back to a tiny favicon.
        if url.lower().split("?")[0].endswith(".svg"):
            svg_png, svg_ct = _try_svg(url)
            if svg_png:
                return svg_png, svg_ct
            continue
        data, ct, min_side = _download(url)
        if not data:
            continue
        if min_side >= MIN_LOGO_PX:
            return data, ct          # crisp - use it
        if best is None or min_side > best[2]:
            best = (data, ct, min_side)

    if best:
        return best[0], best[1]
    return None, None
