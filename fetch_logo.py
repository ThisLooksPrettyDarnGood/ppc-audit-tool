"""
fetch_logo.py
-------------
Download a client logo from their website and return raw bytes + content type.

Heuristics (in priority order):
  1. Apple touch icon  — high-res, square, most reliable logo signal
  2. <link rel="icon"> with sizes attribute — prefer largest
  3. First <img> inside <header> or <nav> — typically the site logo
  4. First PNG <img> on the page — PNGs are usually logos; JPGs are hero images
  5. og:image meta tag
  6. /favicon.ico

Returns (bytes, content_type) or (None, None) if nothing found.
"""

import re
import requests
from urllib.parse import urlparse, urljoin

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PPCAuditTool/1.0)"}
TIMEOUT = 8
MAX_LOGO_BYTES = 150_000   # anything larger is almost certainly a photo, not a logo


def _parse_root(website_url: str) -> str | None:
    url = website_url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else None


# Google Slides' replaceImage only accepts PNG / JPEG / GIF. SVG and ICO are fetched
# fine but FAIL silently at insertion — so we must reject them here.
_SLIDES_OK = ("image/png", "image/jpeg", "image/jpg", "image/gif")


def _download(url: str) -> tuple[bytes, str] | tuple[None, None]:
    """Download an image only if Google Slides can actually use it (PNG/JPEG/GIF)
    and it's under the size cap. HEAD is best-effort — some servers block it."""
    try:
        # Best-effort HEAD to skip oversized files; never bail just because HEAD fails.
        try:
            head = requests.head(url, timeout=TIMEOUT, headers=HEADERS, allow_redirects=True)
            size = int(head.headers.get("Content-Length", 0) or 0)
            if size and size > MAX_LOGO_BYTES:
                return None, None
        except Exception:
            pass

        r = requests.get(url, timeout=TIMEOUT, headers=HEADERS, allow_redirects=True)
        if r.status_code != 200:
            return None, None
        ct = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
        path = url.lower().split("?")[0]
        # Reject anything Slides can't render (SVG, ICO, etc.)
        if "svg" in ct or path.endswith((".svg", ".ico")):
            return None, None
        is_raster = ct in _SLIDES_OK
        if not ct and path.endswith((".png", ".jpg", ".jpeg", ".gif")):
            is_raster, ct = True, "image/png"
        if is_raster and 100 < len(r.content) <= MAX_LOGO_BYTES:
            return r.content, ct or "image/png"
    except Exception:
        pass
    return None, None


def fetch_logo_bytes(website_url: str) -> tuple[bytes, str] | tuple[None, None]:
    """
    Returns (image_bytes, content_type) or (None, None).
    """
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

    candidates: list[str] = []

    if html:
        # 1. Apple touch icon
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
            candidates.append(urljoin(root, m.group(1)))

        # 2. Largest sized <link rel="icon">
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
            candidates.append(icon_url)

        # 3. First <img> inside <header> or <nav>
        for container_tag in ("header", "nav"):
            m = re.search(
                rf'<{container_tag}[^>]*>(.*?)</{container_tag}>',
                html, re.IGNORECASE | re.DOTALL,
            )
            if m:
                img = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', m.group(1), re.IGNORECASE)
                if img:
                    candidates.append(urljoin(root, img.group(1)))
                    break

        # 4. Any <img> whose src URL or alt contains "logo"
        logo_imgs = re.findall(
            r'<img[^>]+(?:src=["\']([^"\']*logo[^"\']*)["\']|alt=["\'][^"\']*logo[^"\']*["\'][^>]+src=["\']([^"\']+)["\'])',
            html, re.IGNORECASE,
        )
        for groups in logo_imgs[:3]:
            img_url = groups[0] or groups[1]
            if img_url:
                candidates.append(urljoin(root, img_url))

        # 5. First few <img> tags on the page in document order (catches sites
        #    where the logo is simply the first image, e.g. no class/alt hints)
        all_imgs = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE)
        for img_url in all_imgs[:5]:
            candidates.append(urljoin(root, img_url))

        # 6. og:image
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
            candidates.append(m.group(1))

    seen: set[str] = set()
    for url in candidates:
        if url in seen:
            continue
        seen.add(url)
        data, ct = _download(url)
        if data:
            return data, ct

    # 7. Reliable raster fallbacks — these ALWAYS return a Slides-compatible PNG, so a
    #    site with only an SVG logo (or one that hides its logo behind JS) still gets one.
    domain = urlparse(root).netloc
    for fallback in (
        f"https://logo.clearbit.com/{domain}?size=200&format=png",   # real brand logo
        f"https://www.google.com/s2/favicons?domain={domain}&sz=128",  # always works
    ):
        data, ct = _download(fallback)
        if data:
            return data, ct

    return None, None
