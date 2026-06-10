import requests
import json
import re
import base64
import pandas as pd
from html import unescape
from datetime import datetime
from urllib.parse import urlparse, parse_qs, unquote

from utils.preview import fetch_generic_preview

GRAPHQL = "https://www.facebook.com/api/graphql/"

BASE_HEADERS = {
    "user-agent": "Mozilla/5.0",
    "content-type": "application/x-www-form-urlencoded"
}

GET_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


# ============================================================
# Helper baru: membersihkan teks HTML/JS Facebook
# ============================================================

def _clean_fb_text(text):
    if text is None:
        return ""

    text = unescape(str(text))

    replacements = {
        "\\/": "/",
        '\\"': '"',
        "\\u0025": "%",
        "\\u0026": "&",
        "\\u003d": "=",
        "\\u003D": "=",
        "\\u003f": "?",
        "\\u003F": "?",
        "\\u002F": "/",
        "\\u002f": "/",
        "\\u003A": ":",
        "\\u003a": ":",
        "\\u002C": ",",
        "\\u002c": ",",
        "\\u0022": '"',
        "\\u003C": "<",
        "\\u003c": "<",
        "\\u003E": ">",
        "\\u003e": ">",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    return text


def _normalize_facebook_url(url):
    url = str(url).strip()

    if url.startswith("facebook.com/"):
        url = "https://" + url
    elif url.startswith("www.facebook.com/"):
        url = "https://" + url
    elif url.startswith("m.facebook.com/"):
        url = "https://" + url
    elif url.startswith("mbasic.facebook.com/"):
        url = "https://" + url

    url = url.replace("https://web.facebook.com/", "https://www.facebook.com/")
    return url


def _make_url_variants(url):
    """
    Tidak mengubah alur besar.
    Ini hanya fallback ringan agar halaman video/reel/share kadang lebih mudah dibaca.
    """
    url = _normalize_facebook_url(url)

    variants = [url]

    if "www.facebook.com" in url:
        variants.append(url.replace("https://www.facebook.com/", "https://m.facebook.com/"))
        variants.append(url.replace("https://www.facebook.com/", "https://mbasic.facebook.com/"))
    elif "facebook.com" in url and "m.facebook.com" not in url and "mbasic.facebook.com" not in url:
        variants.append(url.replace("https://facebook.com/", "https://www.facebook.com/"))
        variants.append(url.replace("https://facebook.com/", "https://m.facebook.com/"))
        variants.append(url.replace("https://facebook.com/", "https://mbasic.facebook.com/"))

    # deduplicate
    out = []
    seen = set()
    for v in variants:
        if v not in seen:
            out.append(v)
            seen.add(v)

    return out


def _fetch_best_facebook_html(url):
    """
    Ambil HTML terbaik dari beberapa varian URL.
    Tetap pure requests, tidak pakai browser/Selenium.
    """
    variants = _make_url_variants(url)

    best = {
        "requested_url": None,
        "final_url": None,
        "status_code": None,
        "html": "",
        "score": -1,
        "error": None,
    }

    for v in variants:
        try:
            r = requests.get(
                v,
                headers=GET_HEADERS,
                timeout=30,
                allow_redirects=True,
            )

            html = r.text or ""
            clean_html = _clean_fb_text(html)

            score = 0

            if r.status_code == 200:
                score += 2

            if 'property="og:url"' in clean_html or "property='og:url'" in clean_html:
                score += 5

            if "ZmVlZGJhY2s6" in clean_html:
                score += 8

            if "feedback_id" in clean_html or "feedbackID" in clean_html or "feedback:" in clean_html:
                score += 8

            if "story_fbid" in clean_html or "post_id" in clean_html or "share_fbid" in clean_html:
                score += 4

            if "/reel/" in clean_html or "/videos/" in clean_html:
                score += 2

            # Hindari memilih halaman login kalau ada pilihan lain
            final_url_lower = (r.url or "").lower()
            if "login" in final_url_lower:
                score -= 5

            if score > best["score"]:
                best = {
                    "requested_url": v,
                    "final_url": r.url,
                    "status_code": r.status_code,
                    "html": html,
                    "score": score,
                    "error": None,
                }

        except Exception as e:
            if best["score"] < 0:
                best = {
                    "requested_url": v,
                    "final_url": None,
                    "status_code": None,
                    "html": "",
                    "score": -1,
                    "error": str(e),
                }

    return best


def _extract_og_url_from_html(html):
    html = _clean_fb_text(html)

    patterns = [
        r'<meta[^>]+property=["\']og:url["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:url["\']',
    ]

    for p in patterns:
        m = re.search(p, html, flags=re.I)
        if m:
            return unescape(m.group(1)).replace("\\/", "/")

    return None


def _extract_canonical_url_from_html(html):
    html = _clean_fb_text(html)

    patterns = [
        r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']',
        r'<link[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\']canonical["\']',
    ]

    for p in patterns:
        m = re.search(p, html, flags=re.I)
        if m:
            return unescape(m.group(1)).replace("\\/", "/")

    return None


def _extract_id_from_url(url):
    """
    Extract ID dari berbagai bentuk URL Facebook.
    Menghasilkan:
    - post biasa
    - pfbid
    - permalink
    - watch video id
    - reel id
    - video id
    """
    if not url:
        return None, None

    url = _clean_fb_text(unquote(str(url)))
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    qs = parse_qs(parsed.query)

    # permalink.php?story_fbid=...&id=...
    story_fbid = qs.get("story_fbid", [None])[0]
    if story_fbid:
        return story_fbid, "permalink"

    # photo.php?fbid=...
    fbid = qs.get("fbid", [None])[0]
    if fbid:
        return fbid, "photo"

    # watch/?v=...
    video_id = qs.get("v", [None])[0]
    if "watch" in path and video_id:
        return video_id, "watch_video"

    parts = [p for p in path.split("/") if p]

    # /{owner}/posts/{post_id}
    # /{owner}/posts/{slug}/{numeric_id}
    # /{owner}/posts/pfbid...
    if "posts" in parts:
        idx = parts.index("posts")
        after = parts[idx + 1:]

        # Prioritaskan dari kanan, karena kadang bentuknya /posts/slug/123
        for item in reversed(after):
            item = item.strip("/")
            if re.fullmatch(r"\d{5,}", item):
                return item, "post"
            if re.fullmatch(r"pfbid[A-Za-z0-9]+", item):
                return item, "post_pfbid"

        # fallback kalau struktur tidak umum
        if after:
            return after[-1].strip("/"), "post_unknown"

    # /reel/{id}, /{owner}/reel/{id}
    for key in ["reel", "reels"]:
        if key in parts:
            idx = parts.index(key)
            if idx + 1 < len(parts):
                return parts[idx + 1].strip("/"), "reel"

    # /{owner}/videos/{id}
    for key in ["videos", "video"]:
        if key in parts:
            idx = parts.index(key)
            if idx + 1 < len(parts):
                return parts[idx + 1].strip("/"), "video"

    return None, None


def _extract_owner_from_url(url):
    """
    Ini tidak wajib untuk GraphQL comments, tapi berguna untuk debug.
    """
    if not url:
        return None

    url = _clean_fb_text(unquote(str(url)))
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    qs = parse_qs(parsed.query)

    owner_id = qs.get("id", [None])[0]
    if owner_id:
        return owner_id

    parts = [p for p in path.split("/") if p]
    if not parts:
        return None

    if parts[0] in ["watch", "reel", "reels", "video", "videos", "permalink.php", "story.php", "photo.php"]:
        return None

    return parts[0]


def _convert_feedback_id(post_id):
    """
    Cara lama tetap dipertahankan.
    Bedanya: sekarang post_id bisa numeric atau pfbid.
    """
    if not post_id:
        return None

    return base64.b64encode(f"feedback:{post_id}".encode()).decode()


def _is_base64_feedback_id(value):
    if not value:
        return False

    value = str(value).strip()

    if not value.startswith("ZmVlZGJhY2s6"):
        return False

    try:
        padded = value + ("=" * (-len(value) % 4))
        decoded = base64.b64decode(padded).decode("utf-8", errors="ignore")
        return decoded.startswith("feedback:")
    except Exception:
        return False


def _normalize_feedback_id(value):
    """
    Menormalkan kandidat feedback id:
    - kalau sudah base64 feedback, pakai langsung
    - kalau bentuknya feedback:xxx, encode
    - kalau hanya angka/pfbid, jadikan feedback:xxx lalu encode
    """
    if not value:
        return None

    value = _clean_fb_text(str(value)).strip()
    value = value.strip('"').strip("'").strip()

    # Kadang ada encoded unicode / URL
    value = unquote(value)

    if _is_base64_feedback_id(value):
        return value

    if value.startswith("feedback:"):
        return base64.b64encode(value.encode()).decode()

    if re.fullmatch(r"\d{5,}", value):
        return _convert_feedback_id(value)

    if re.fullmatch(r"pfbid[A-Za-z0-9]+", value):
        return _convert_feedback_id(value)

    return None


def _extract_feedback_id_from_html(html):
    """
    Metode utama tambahan:
    Cari feedback_id langsung dari HTML/JS Facebook.

    Ini penting untuk reel/video karena video_id tidak selalu sama dengan
    feedback target yang dipakai untuk komentar.
    """
    text = _clean_fb_text(html)

    candidates = []

    # 1. Base64 feedback id langsung.
    # Base64 dari "feedback:" biasanya diawali "ZmVlZGJhY2s6"
    for m in re.finditer(r"(ZmVlZGJhY2s6[A-Za-z0-9+/=]+)", text):
        candidates.append(m.group(1))

    # 2. Pola JSON umum
    patterns = [
        r'"feedback_id"\s*:\s*"([^"]+)"',
        r'"feedbackID"\s*:\s*"([^"]+)"',
        r'"feedbackTargetID"\s*:\s*"([^"]+)"',
        r'"subscription_target_id"\s*:\s*"([^"]+)"',

        # feedback:123 atau feedback:pfbid...
        r"(feedback:(?:\d{5,}|pfbid[A-Za-z0-9]+))",

        # Dalam object feedback {... "id": "..."}
        r'"feedback"\s*:\s*\{[^{}]{0,1500}?"id"\s*:\s*"([^"]+)"',

        # Kadang ada node feedback dengan id agak jauh
        r'"feedback"\s*:\s*\{.{0,3000}?"id"\s*:\s*"([^"]+)"',
    ]

    for p in patterns:
        for m in re.finditer(p, text, flags=re.I | re.S):
            candidates.append(m.group(1))

    # 3. Ambil kandidat pertama yang valid sebagai feedback id
    for c in candidates:
        fid = _normalize_feedback_id(c)
        if fid:
            return fid

    return None


def _extract_ids_from_html(html):
    """
    Fallback: ambil post/story/video/reel id dari HTML.
    """
    text = _clean_fb_text(html)

    patterns = [
        ("share_fbid", r'"share_fbid"\s*:\s*"([^"]+)"'),
        ("story_fbid", r'"story_fbid"\s*:\s*"([^"]+)"'),
        ("post_id", r'"post_id"\s*:\s*"([^"]+)"'),
        ("top_level_post_id", r'"top_level_post_id"\s*:\s*"([^"]+)"'),
        ("video_id", r'"video_id"\s*:\s*"(\d+)"'),
        ("reel_id", r'"reel_id"\s*:\s*"(\d+)"'),
        ("legacy_fbid", r'"legacy_fbid"\s*:\s*"(\d+)"'),
    ]

    found = {}

    for key, pattern in patterns:
        m = re.search(pattern, text, flags=re.I | re.S)
        if m:
            found[key] = m.group(1)

    return found


def _extract_facebook_target(url):
    """
    Resolver baru.
    Output utama:
    - feedback_id: paling penting untuk GraphQL comments
    - post_id: fallback kalau feedback_id tidak ditemukan
    - object_type: post/reel/video/watch/permalink
    """

    url = _normalize_facebook_url(url)

    # 1. Ambil HTML terbaik
    info = _fetch_best_facebook_html(url)
    html = info.get("html") or ""

    if not html:
        raise Exception(
            "HTML tidak berhasil diambil. "
            f"requested_url={info.get('requested_url')} | "
            f"final_url={info.get('final_url')} | "
            f"status_code={info.get('status_code')} | "
            f"score={info.get('score')} | "
            f"error={info.get('error')}"
        )
  
    # 2. Ambil URL metadata kalau ada
    og_url = _extract_og_url_from_html(html)
    canonical_url = _extract_canonical_url_from_html(html)
    final_url = info.get("final_url")

    # 3. Utamakan feedback_id langsung dari HTML
    feedback_id = _extract_feedback_id_from_html(html)

    # 4. Ambil post/video/reel id dari beberapa sumber URL
    candidates = []

    for source_name, source_url in [
        ("input_url", url),
        ("final_url", final_url),
        ("og_url", og_url),
        ("canonical_url", canonical_url),
    ]:
        extracted_id, object_type = _extract_id_from_url(source_url)
        if extracted_id:
            candidates.append({
                "id": extracted_id,
                "type": object_type,
                "source": source_name,
                "url": source_url,
            })

    # 5. Fallback ambil ID dari HTML
    html_ids = _extract_ids_from_html(html)

    for key in [
        "share_fbid",
        "story_fbid",
        "post_id",
        "top_level_post_id",
        "legacy_fbid",
        "video_id",
        "reel_id",
    ]:
        if html_ids.get(key):
            candidates.append({
                "id": html_ids[key],
                "type": key,
                "source": "html",
                "url": None,
            })

    # 6. Pilih ID terbaik.
    # Untuk post biasa, post_id/story_fbid lebih aman.
    # Untuk reel/video, kalau tidak ada post wrapper, pakai video/reel id.
    chosen = None

    priority = [
        "post",
        "post_pfbid",
        "permalink",
        "story_fbid",
        "share_fbid",
        "post_id",
        "top_level_post_id",
        "legacy_fbid",
        "video",
        "watch_video",
        "reel",
        "video_id",
        "reel_id",
        "photo",
        "post_unknown",
    ]

    for p in priority:
        for c in candidates:
            if c["type"] == p:
                chosen = c
                break
        if chosen:
            break

    if not chosen and candidates:
        chosen = candidates[0]

    post_id = chosen["id"] if chosen else None
    object_type = chosen["type"] if chosen else None

    # 7. Kalau feedback_id belum ada, fallback ke cara lama
    if not feedback_id and post_id:
        feedback_id = _convert_feedback_id(post_id)

    if not feedback_id:
        # Pertahankan rasa error lama, tapi lebih informatif
        if not og_url:
            raise Exception(
                "feedback_id dan og:url tidak ditemukan. "
                "Link mungkin tidak public, butuh login, atau konten tidak didukung."
            )
        raise Exception("Feedback ID/Post ID tidak ditemukan dari URL/HTML.")

    return {
        "feedback_id": feedback_id,
        "post_id": post_id,
        "object_type": object_type,
        "input_url": url,
        "final_url": final_url,
        "og_url": og_url,
        "canonical_url": canonical_url,
        "html_source_url": info.get("requested_url"),
    }


def _extract_post_id(url):
    """
    Fungsi lama tetap ada agar kompatibel dengan bagian lain.
    Sekarang isinya memakai resolver baru.
    """
    target = _extract_facebook_target(url)

    if not target.get("post_id"):
        raise Exception("Post ID tidak ditemukan dari URL.")

    return target["post_id"]


def _fb_json(text):
    text = text.strip()
    if text.startswith("for (;;);"):
        text = text[len("for (;;);"):]

    first_line = text.split("\n")[0].strip()
    return json.loads(first_line)


def _format_date(ts):
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%b %d, %Y")
    except Exception:
        return None


def _clean_comment(text):
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    text = text.replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _comments_payload(feedback_id, cursor=None):
    return {
        "av": "0",
        "__user": "0",
        "__a": "1",
        "doc_id": "25550760954572974",
        "variables": json.dumps({
            "commentsAfterCount": -1,
            "commentsAfterCursor": cursor,
            "commentsIntentToken": "REVERSE_CHRONOLOGICAL_UNFILTERED_INTENT_V1",
            "feedLocation": "DEDICATED_COMMENTING_SURFACE",
            "focusCommentID": None,
            "scale": 2,
            "useDefaultActor": False,
            "id": feedback_id
        })
    }


def _replies_payload(comment_feedback_id, expansion_token):
    return {
        "av": "0",
        "__user": "0",
        "__a": "1",
        "doc_id": "26570577339199586",
        "variables": json.dumps({
            "clientKey": None,
            "expansionToken": expansion_token,
            "feedLocation": "POST_PERMALINK_DIALOG",
            "focusCommentID": None,
            "scale": 2,
            "useDefaultActor": False,
            "id": comment_feedback_id
        })
    }


def _fetch_comments(feedback_id):
    results = []
    cursor = None

    while True:
        headers = {**BASE_HEADERS, "x-fb-friendly-name": "CommentsListComponentsPaginationQuery"}
        r = requests.post(
            GRAPHQL,
            headers=headers,
            data=_comments_payload(feedback_id, cursor),
            timeout=30
        )
        r.raise_for_status()

        j = _fb_json(r.text)

        comments_block = (
            j.get("data", {})
             .get("node", {})
             .get("comment_rendering_instance_for_feed_location", {})
             .get("comments", {})
        )

        edges = comments_block.get("edges", [])
        if not edges:
            break

        for e in edges:
            n = e.get("node", {})
            fb = n.get("feedback", {})
            expansion_info = fb.get("expansion_info", {}) or {}

            created_time = n.get("created_time")

            results.append({
                "author": (n.get("author") or {}).get("name", ""),
                "comment_text": (n.get("body") or {}).get("text", ""),
                "comment_reaction_count": fb.get("reactors", {}).get("count_reduced", "0"),
                "timestamp": created_time,
                "_feedback_id": fb.get("id"),
                "_expansion_token": expansion_info.get("expansion_token")
            })

        cursor = comments_block.get("page_info", {}).get("end_cursor")
        if not cursor:
            break

    return results


def _fetch_replies(comment):
    if not comment.get("_feedback_id") or not comment.get("_expansion_token"):
        return []

    headers = {**BASE_HEADERS, "x-fb-friendly-name": "Depth1CommentsListPaginationQuery"}

    r = requests.post(
        GRAPHQL,
        headers=headers,
        data=_replies_payload(comment["_feedback_id"], comment["_expansion_token"]),
        timeout=30
    )
    r.raise_for_status()

    j = _fb_json(r.text)

    edges = (
        j.get("data", {})
         .get("node", {})
         .get("replies_connection", {})
         .get("edges", [])
    )

    replies = []
    for e in edges:
        n = e.get("node", {})
        fb = n.get("feedback", {})
        created_time = n.get("created_time")

        replies.append({
            "author": (n.get("author") or {}).get("name", ""),
            "reply_text": (n.get("body") or {}).get("text", ""),
            "reply_reaction_count": fb.get("reactors", {}).get("count_reduced", "0"),
            "timestamp": created_time
        })

    return replies


def extract_facebook_comments(url: str, progress_callback=None, with_preview=False):
    preview = fetch_generic_preview(url) if with_preview else {}

    if preview:
        # Facebook: tampilkan Author + Description
        author_name = (preview.get("title") or preview.get("author") or "Facebook").strip()
        description = (preview.get("description") or "").strip()

        preview = {
            "title": None,
            "description": description,
            "image": preview.get("image"),
            "author": author_name,
            "engagement": None,
            "mode": "facebook",
        }

    if progress_callback:
        progress_callback("Finding feedback/post ID...", 10)

    # ========================================================
    # Bagian utama yang berubah:
    # Dulu: post_id = _extract_post_id(url)
    #       feedback_id = _convert_feedback_id(post_id)
    #
    # Sekarang:
    #       cari feedback_id langsung dulu.
    #       kalau gagal, resolver otomatis fallback ke feedback:{post_id}
    # ========================================================
    target = _extract_facebook_target(url)

    post_id = target.get("post_id")
    feedback_id = target.get("feedback_id")

    if progress_callback:
        object_type = target.get("object_type") or "unknown"
        progress_callback(f"Detected target type: {object_type}", 20)

    if progress_callback:
        progress_callback("Extracting parent comments...", 30)

    comments = _fetch_comments(feedback_id)
    rows = []

    total_parents = len(comments) if comments else 1

    for i, c in enumerate(comments, start=1):
        if progress_callback:
            progress = min(90, 30 + int((i / total_parents) * 55))
            progress_callback(f"Processing thread {i}/{total_parents}...", progress)

        rows.append({
            "date": _format_date(c["timestamp"]),
            "author": c["author"],
            "type": "parent",
            "comment": _clean_comment(c["comment_text"]),
            "like": int(c["comment_reaction_count"]) if str(c["comment_reaction_count"]).isdigit() else 0
        })

        replies = _fetch_replies(c)
        for r in replies:
            rows.append({
                "date": _format_date(r["timestamp"]),
                "author": r["author"],
                "type": "reply",
                "comment": _clean_comment(r["reply_text"]),
                "like": int(r["reply_reaction_count"]) if str(r["reply_reaction_count"]).isdigit() else 0
            })

    if progress_callback:
        progress_callback("Formatting results...", 95)

    if not rows:
        empty_df = pd.DataFrame(columns=["index", "date", "author", "type", "comment", "like"])
        return empty_df, preview

    df = pd.DataFrame(rows)
    df.insert(0, "index", range(1, len(df) + 1))
    df = df[["index", "date", "author", "type", "comment", "like"]]

    if progress_callback:
        progress_callback("Done", 100)

    return df, preview
