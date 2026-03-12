import json
import re
import requests
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}


def _get_meta(soup, prop=None, name=None):
    if prop:
        tag = soup.find("meta", property=prop)
        if tag and tag.get("content"):
            return tag["content"]

    if name:
        tag = soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return tag["content"]

    return None


def fetch_generic_preview(url: str) -> dict:
    try:
        r = requests.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
        r.raise_for_status()

        soup = BeautifulSoup(r.text, "html.parser")

        title = _get_meta(soup, prop="og:title") or _get_meta(soup, name="title")
        description = _get_meta(soup, prop="og:description") or _get_meta(soup, name="description")
        image = _get_meta(soup, prop="og:image")
        author = _get_meta(soup, name="author") or _get_meta(soup, prop="article:author")
        site_name = _get_meta(soup, prop="og:site_name")

        return {
            "title": title,
            "description": description,
            "image": image,
            "author": author or site_name,
            "engagement": None,
            "mode": "default",
        }

    except Exception:
        return {}

def fetch_tiktok_preview(url: str) -> dict:
    try:
        html = _fetch_html(url)
        soup = BeautifulSoup(html, "html.parser")

        # 1) Coba dari meta tag biasa dulu
        title = _get_meta(soup, prop="og:title")
        description = _get_meta(soup, prop="og:description")
        image = _get_meta(soup, prop="og:image")
        site_name = _get_meta(soup, prop="og:site_name")

        preview = {
            "title": title,
            "description": description,
            "image": image,
            "author": site_name or "TikTok",
            "engagement": None,
        }

        # Kalau sudah cukup, langsung return
        if preview["title"] or preview["description"] or preview["image"]:
            return preview

        # 2) Fallback: ambil dari __UNIVERSAL_DATA_FOR_REHYDRATION__
        m = re.search(
            r'<script[^>]*id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
            html,
            re.DOTALL
        )
        if m:
            try:
                data = json.loads(m.group(1))
                default_scope = data.get("__DEFAULT_SCOPE__", {})

                item_struct = None
                for scope_key in ("webapp.video-detail", "webapp.photo-detail"):
                    item_struct = (
                        default_scope.get(scope_key, {})
                        .get("itemInfo", {})
                        .get("itemStruct", {})
                    )
                    if item_struct:
                        break

                if item_struct:
                    author_info = item_struct.get("author", {}) or {}
                    stats = item_struct.get("stats", {}) or {}

                    return {
                        "title": item_struct.get("desc"),
                        "description": item_struct.get("desc"),
                        "image": (
                            item_struct.get("video", {}) or {}
                        ).get("cover")
                        or (
                            item_struct.get("video", {}) or {}
                        ).get("originCover")
                        or (
                            item_struct.get("imagePost", {}) or {}
                        ).get("cover", {})
                        .get("imageURL", {})
                        .get("urlList", [None])[0],
                        "author": author_info.get("nickname") or author_info.get("uniqueId") or "TikTok",
                        "engagement": (
                            f"❤️ {stats.get('diggCount', 0)} | "
                            f"💬 {stats.get('commentCount', 0)} | "
                            f"↪️ {stats.get('shareCount', 0)}"
                        ),
                    }
            except Exception:
                pass

        # 3) Fallback: SIGI_STATE
        m = re.search(
            r'<script[^>]*id="SIGI_STATE"[^>]*>(.*?)</script>',
            html,
            re.DOTALL
        )
        if m:
            try:
                data = json.loads(m.group(1))
                item_module = data.get("ItemModule") or {}

                if isinstance(item_module, dict) and item_module:
                    first_item = next(iter(item_module.values()))
                    if isinstance(first_item, dict):
                        author_info = first_item.get("author") or {}
                        stats = first_item.get("stats") or {}

                        return {
                            "title": first_item.get("desc"),
                            "description": first_item.get("desc"),
                            "image": first_item.get("video", {}).get("cover"),
                            "author": author_info.get("nickname") or author_info.get("uniqueId") or "TikTok",
                            "engagement": (
                                f"❤️ {stats.get('diggCount', 0)} | "
                                f"💬 {stats.get('commentCount', 0)} | "
                                f"↪️ {stats.get('shareCount', 0)}"
                            ),
                        }
            except Exception:
                pass

        return preview

    except Exception:
        return {}


def fetch_preview(url: str) -> dict:
    url_lower = url.lower()

    if "tiktok.com" in url_lower:
        return fetch_tiktok_preview(url)

    return fetch_generic_preview(url)