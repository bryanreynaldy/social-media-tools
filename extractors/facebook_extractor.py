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

COMMENTS_INTENT_TOKEN = "RANKED_UNFILTERED_CHRONOLOGICAL_REPLIES_INTENT_V1"
MAX_STAGNANT_PAGES = 3


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
    url = _normalize_facebook_url(url)

    variants = [url]

    if "www.facebook.com" in url:
        variants.append(url.replace("https://www.facebook.com/", "https://m.facebook.com/"))
        variants.append(url.replace("https://www.facebook.com/", "https://mbasic.facebook.com/"))
    elif "facebook.com" in url and "m.facebook.com" not in url and "mbasic.facebook.com" not in url:
        variants.append(url.replace("https://facebook.com/", "https://www.facebook.com/"))
        variants.append(url.replace("https://facebook.com/", "https://m.facebook.com/"))
        variants.append(url.replace("https://facebook.com/", "https://mbasic.facebook.com/"))

    out = []
    seen = set()
    for v in variants:
        if v not in seen:
            out.append(v)
            seen.add(v)

    return out


def _fetch_best_facebook_html(url, session=None):
    session = session or requests.Session()
    variants = _make_url_variants(url)

    best = {
        "requested_url": None,
        "final_url": None,
        "status_code": None,
        "html": "",
        "score": -9999,
        "error": None,
    }

    for v in variants:
        try:
            r = session.get(
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
            if best["score"] == -9999:
                best = {
                    "requested_url": v,
                    "final_url": None,
                    "status_code": None,
                    "html": "",
                    "score": -9999,
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


def _extract_lsd_from_html(html):
    text = _clean_fb_text(html)

    patterns = [
        r'"LSD"\s*,\s*\[\]\s*,\s*\{\s*"token"\s*:\s*"([^"]+)"',
        r'name=\\?"lsd\\?"\s+value=\\?"([^"\\]+)\\?"',
        r'"lsd"\s*:\s*"([^"]+)"',
    ]

    for p in patterns:
        m = re.search(p, text, flags=re.I)
        if m:
            return m.group(1)

    return None


def _compute_jazoest(token):
    if not token:
        return None
    total = sum(ord(c) for c in token)
    return f"2{total}"


def _extract_id_from_url(url):
    if not url:
        return None, None

    url = _clean_fb_text(unquote(str(url)))
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    qs = parse_qs(parsed.query)

    story_fbid = qs.get("story_fbid", [None])[0]
    if story_fbid:
        return story_fbid, "permalink"

    fbid = qs.get("fbid", [None])[0]
    if fbid:
        return fbid, "photo"

    video_id = qs.get("v", [None])[0]
    if "watch" in path and video_id:
        return video_id, "watch_video"

    parts = [p for p in path.split("/") if p]

    if "posts" in parts:
        idx = parts.index("posts")
        after = parts[idx + 1:]

        for item in reversed(after):
            item = item.strip("/")
            if re.fullmatch(r"\d{5,}", item):
                return item, "post"
            if re.fullmatch(r"pfbid[A-Za-z0-9]+", item):
                return item, "post_pfbid"

        if after:
            return after[-1].strip("/"), "post_unknown"

    for key in ["reel", "reels"]:
        if key in parts:
            idx = parts.index(key)
            if idx + 1 < len(parts):
                return parts[idx + 1].strip("/"), "reel"

    for key in ["videos", "video"]:
        if key in parts:
            idx = parts.index(key)
            if idx + 1 < len(parts):
                return parts[idx + 1].strip("/"), "video"

    return None, None


def _extract_owner_from_url(url):
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
    if not post_id:
        return None

    return base64.b64encode(f"feedback:{post_id}".encode()).decode()


def _is_base64_feedback_id(value):
    if not value:
        return False

    value = str(value).strip()

    if not value.startswith("ZmVlZGJhY2s6"):
        return False

    if len(value) > 80:
        return False

    try:
        padded = value + ("=" * (-len(value) % 4))
        decoded = base64.b64decode(padded).decode("utf-8", errors="strict")
        return bool(re.fullmatch(r"feedback:(\d+|pfbid[A-Za-z0-9_-]+)", decoded))
    except Exception:
        return False


def _normalize_feedback_id(value):
    if not value:
        return None

    value = _clean_fb_text(str(value)).strip()
    value = value.strip('"').strip("'").strip()

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
    text = _clean_fb_text(html)

    candidates = []

    for m in re.finditer(r"(ZmVlZGJhY2s6[A-Za-z0-9+/=]{1,80})", text):
        candidates.append(m.group(1))

    patterns = [
        r'"feedback_id"\s*:\s*"([^"]+)"',
        r'"feedbackID"\s*:\s*"([^"]+)"',
        r'"feedbackTargetID"\s*:\s*"([^"]+)"',
        r'"subscription_target_id"\s*:\s*"([^"]+)"',
        r"(feedback:(?:\d{5,}|pfbid[A-Za-z0-9]+))",
        r'"feedback"\s*:\s*\{[^{}]{0,1500}?"id"\s*:\s*"([^"]+)"',
        r'"feedback"\s*:\s*\{.{0,3000}?"id"\s*:\s*"([^"]+)"',
    ]

    for p in patterns:
        for m in re.finditer(p, text, flags=re.I | re.S):
            candidates.append(m.group(1))

    for c in candidates:
        fid = _normalize_feedback_id(c)
        if fid:
            return fid

    return None


def _extract_ids_from_html(html):
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


def _extract_facebook_target(url, session=None):
    session = session or requests.Session()
    url = _normalize_facebook_url(url)

    info = _fetch_best_facebook_html(url, session)
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

    og_url = _extract_og_url_from_html(html)
    canonical_url = _extract_canonical_url_from_html(html)
    final_url = info.get("final_url")

    lsd = _extract_lsd_from_html(html)

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

    feedback_id = _convert_feedback_id(post_id) if post_id else None

    if not feedback_id:
        feedback_id = _extract_feedback_id_from_html(html)

    if not feedback_id:
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
        "lsd": lsd,
    }


def _extract_post_id(url, session=None):
    target = _extract_facebook_target(url, session)

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


def _reply_record_from_node(node):
    node = node or {}
    feedback = node.get("feedback") or {}
    return {
        "author": (node.get("author") or {}).get("name", ""),
        "reply_text": (node.get("body") or {}).get("text", ""),
        "reply_reaction_count": (feedback.get("reactors") or {}).get(
            "count_reduced", (feedback.get("reactors") or {}).get("count", "0")
        ),
        "timestamp": node.get("created_time"),
        "_reply_id": node.get("id") or feedback.get("id")
    }


def _extract_inline_replies(comment_node):
    comment_node = comment_node or {}
    feedback = comment_node.get("feedback") or {}
    connections = [
        comment_node.get("replies_connection"),
        feedback.get("replies_connection"),
        comment_node.get("display_comments"),
        feedback.get("display_comments")
    ]

    replies = []
    seen = set()

    for connection in connections:
        if not connection:
            continue

        if isinstance(connection, dict):
            items = connection.get("edges") or connection.get("nodes") or []
        elif isinstance(connection, list):
            items = connection
        else:
            continue

        for item in items:
            node = (item or {}).get("node") if isinstance(item, dict) and "node" in item else item
            if not isinstance(node, dict):
                continue

            record = _reply_record_from_node(node)
            reply_id = record.pop("_reply_id", None)
            fallback_key = (
                record.get("author"),
                record.get("timestamp"),
                _clean_comment(record.get("reply_text"))
            )
            dedup_key = reply_id or fallback_key

            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            replies.append(record)

    return replies


def _comments_payload(feedback_id, lsd, jazoest, cursor=None, comments_intent_token=COMMENTS_INTENT_TOKEN):
    return {
        "av": "0",
        "__user": "0",
        "__a": "1",
        "dpr": "1",
        "server_timestamps": "true",
        "fb_api_caller_class": "RelayModern",
        "fb_api_req_friendly_name": "CommentsListComponentsPaginationQuery",
        "lsd": lsd,
        "jazoest": jazoest,
        "doc_id": "27806180149070312",
        "variables": json.dumps({
            "commentsAfterCount": -1,
            "commentsAfterCursor": cursor,
            "commentsBeforeCount": None,
            "commentsBeforeCursor": None,
            "commentsIntentToken": comments_intent_token,
            "feedLocation": "TAHOE",
            "focusCommentID": None,
            "scale": 1,
            "useDefaultActor": False,
            "id": feedback_id,
            "__relay_internal__pv__CometUFICommentAutoTranslationTyperelayprovider": "AUTO_TRANSLATE",
            "__relay_internal__pv__CometUFICommentAvatarStickerAnimatedImagerelayprovider": False,
            "__relay_internal__pv__CometUFICommentActionLinksRewriteEnabledrelayprovider": False,
            "__relay_internal__pv__IsWorkUserrelayprovider": False
        })
    }


def _replies_payload(comment_feedback_id, expansion_token, lsd, jazoest, cursor=None, feed_location="POST_PERMALINK_DIALOG"):
    return {
        "av": "0",
        "__user": "0",
        "__a": "1",
        "dpr": "1",
        "server_timestamps": "true",
        "fb_api_caller_class": "RelayModern",
        "fb_api_req_friendly_name": "Depth1CommentsListPaginationQuery",
        "lsd": lsd,
        "jazoest": jazoest,
        "doc_id": "26570577339199586",
        "variables": json.dumps({
            "commentsAfterCount": 50,
            "commentsAfterCursor": cursor,
            "commentsBeforeCount": None,
            "commentsBeforeCursor": None,
            "commentsIntentToken": None,
            "clientKey": None,
            "expansionToken": expansion_token,
            "feedLocation": feed_location,
            "focusCommentID": None,
            "scale": 1,
            "useDefaultActor": False,
            "id": comment_feedback_id,
            "__relay_internal__pv__CometUFICommentAutoTranslationTyperelayprovider": "AUTO_TRANSLATE",
            "__relay_internal__pv__CometUFICommentAvatarStickerAnimatedImagerelayprovider": False,
            "__relay_internal__pv__CometUFICommentActionLinksRewriteEnabledrelayprovider": False,
            "__relay_internal__pv__IsWorkUserrelayprovider": False
        })
    }

def _fetch_comments(feedback_id, session=None, lsd=None, jazoest=None, max_pages=500, progress_callback=None):
    session = session or requests.Session()
    results = []
    seen_comment_ids = set()
    seen_cursors = set()
    cursor = None
    page_count = 0
    stagnant_pages = 0
    comments_intent_token = COMMENTS_INTENT_TOKEN

    while page_count < max_pages:
        page_count += 1

        if progress_callback:
            progress = min(50, 23 + page_count * 2)
            progress_callback(
                f"Extracting parent comments: page {page_count} ({len(results)} found)...",
                progress
            )

        headers = {**BASE_HEADERS, "x-fb-friendly-name": "CommentsListComponentsPaginationQuery"}
        r = session.post(
            GRAPHQL,
            headers=headers,
            data=_comments_payload(feedback_id, lsd, jazoest, cursor, comments_intent_token),
            timeout=30
        )
        r.raise_for_status()

        j = _fb_json(r.text)
        node = j.get("data", {}).get("node")

        if node is None and comments_intent_token is not None:
            comments_intent_token = None
            page_count -= 1
            continue

        if node is None:
            if results:
                break
            raise Exception(f"GraphQL rejected request: {json.dumps(j)[:800]}")

        comments_block = (
            node.get("comment_rendering_instance_for_feed_location", {})
                .get("comments", {})
        )

        edges = comments_block.get("edges") or []
        if not edges:
            break

        added = 0
        for e in edges:
            n = e.get("node") or {}
            fb = n.get("feedback") or {}
            expansion_info = fb.get("expansion_info") or {}
            comment_id = n.get("id") or fb.get("id")
            fallback_key = (
                (n.get("author") or {}).get("name", ""),
                n.get("created_time"),
                _clean_comment((n.get("body") or {}).get("text", ""))
            )
            dedup_key = comment_id or fallback_key

            if dedup_key in seen_comment_ids:
                continue
            seen_comment_ids.add(dedup_key)

            results.append({
                "author": (n.get("author") or {}).get("name", ""),
                "comment_text": (n.get("body") or {}).get("text", ""),
                "comment_reaction_count": (fb.get("reactors") or {}).get(
                    "count_reduced", (fb.get("reactors") or {}).get("count", "0")
                ),
                "timestamp": n.get("created_time"),
                "_feedback_id": fb.get("id"),
                "_expansion_token": (
                    expansion_info.get("expansion_token")
                    or expansion_info.get("token")
                    or fb.get("expansion_token")
                ),
                "_inline_replies": _extract_inline_replies(n)
            })
            added += 1

        stagnant_pages = stagnant_pages + 1 if added == 0 else 0

        page_info = comments_block.get("page_info") or {}
        next_cursor = page_info.get("end_cursor")
        has_next_page = page_info.get("has_next_page")

        if not next_cursor or has_next_page is False:
            break
        if next_cursor == cursor or next_cursor in seen_cursors:
            break
        if stagnant_pages >= MAX_STAGNANT_PAGES:
            break

        seen_cursors.add(next_cursor)
        cursor = next_cursor

    return results

def _fetch_replies(comment, session=None, lsd=None, jazoest=None, max_pages=200, page_callback=None):
    session = session or requests.Session()
    feedback_id = comment.get("_feedback_id")
    expansion_token = comment.get("_expansion_token")
    replies = list(comment.get("_inline_replies") or [])
    seen_ids = {
        (
            r.get("author"),
            r.get("timestamp"),
            _clean_comment(r.get("reply_text"))
        )
        for r in replies
    }

    if not feedback_id or not expansion_token:
        return replies

    headers = {**BASE_HEADERS, "x-fb-friendly-name": "Depth1CommentsListPaginationQuery"}
    seen_cursors = set()
    cursor = None
    page_count = 0
    stagnant_pages = 0
    working_feed_location = None

    while page_count < max_pages:
        page_count += 1

        if page_callback:
            page_callback(page_count, len(replies))

        node = None
        locations = [working_feed_location] if working_feed_location else ["POST_PERMALINK_DIALOG", "TAHOE"]

        for feed_location in locations:
            try:
                r = session.post(
                    GRAPHQL,
                    headers=headers,
                    data=_replies_payload(feedback_id, expansion_token, lsd, jazoest, cursor, feed_location),
                    timeout=30
                )
                r.raise_for_status()
                j = _fb_json(r.text)
                node = j.get("data", {}).get("node")
                if node is not None:
                    working_feed_location = feed_location
                    break
            except Exception:
                node = None

        if node is None and working_feed_location:
            working_feed_location = None
            continue
        if node is None:
            break

        connection = node.get("replies_connection") or {}
        edges = connection.get("edges") or []
        added = 0

        for e in edges:
            n = e.get("node") or {}
            record = _reply_record_from_node(n)
            reply_id = record.pop("_reply_id", None)
            fallback_key = (
                record.get("author"),
                record.get("timestamp"),
                _clean_comment(record.get("reply_text"))
            )
            dedup_key = reply_id or fallback_key

            if dedup_key in seen_ids or fallback_key in seen_ids:
                continue

            seen_ids.add(dedup_key)
            seen_ids.add(fallback_key)
            replies.append(record)
            added += 1

        stagnant_pages = stagnant_pages + 1 if added == 0 else 0

        page_info = connection.get("page_info") or {}
        next_cursor = page_info.get("end_cursor")
        has_next_page = page_info.get("has_next_page")

        if not next_cursor or has_next_page is False:
            break
        if next_cursor == cursor or next_cursor in seen_cursors:
            break
        if stagnant_pages >= MAX_STAGNANT_PAGES:
            break

        seen_cursors.add(next_cursor)
        cursor = next_cursor

    return replies

def extract_facebook_comments(url: str, progress_callback=None, with_preview=False):
    preview = {}
    session = requests.Session()

    if with_preview:
        if progress_callback:
            progress_callback("Loading Facebook preview...", 5)

        try:
            raw_preview = fetch_generic_preview(url) or {}
        except Exception:
            raw_preview = {}

        if raw_preview:
            author_name = (raw_preview.get("title") or raw_preview.get("author") or "Facebook").strip()
            description = (raw_preview.get("description") or "").strip()
            preview = {
                "title": None,
                "description": description,
                "image": raw_preview.get("image"),
                "author": author_name,
                "engagement": None,
                "mode": "facebook",
            }

    if progress_callback:
        progress_callback("Finding feedback/post ID...", 10)

    target = _extract_facebook_target(url, session=session)
    feedback_id = target.get("feedback_id")
    lsd = target.get("lsd")
    jazoest = _compute_jazoest(lsd)

    if progress_callback:
        object_type = target.get("object_type") or "unknown"
        progress_callback(f"Detected target type: {object_type}", 20)
        progress_callback("Extracting parent comments...", 23)

    comments = _fetch_comments(
        feedback_id,
        session=session,
        lsd=lsd,
        jazoest=jazoest,
        progress_callback=progress_callback
    )
    rows = []
    total_parents = len(comments)

    if progress_callback:
        progress_callback(f"Found {total_parents} parent comments", 55)

    for i, c in enumerate(comments, start=1):
        progress = min(90, 55 + int((i / max(total_parents, 1)) * 35))

        if progress_callback:
            progress_callback(f"Processing thread {i}/{total_parents}...", progress)

        rows.append({
            "date": _format_date(c["timestamp"]),
            "author": c["author"],
            "type": "parent",
            "comment": _clean_comment(c["comment_text"]),
            "like": int(c["comment_reaction_count"]) if str(c["comment_reaction_count"]).isdigit() else 0
        })

        def _reply_page_progress(page_number, reply_count, thread_index=i, thread_total=total_parents, value=progress):
            if progress_callback:
                progress_callback(
                    f"Processing thread {thread_index}/{thread_total}: reply page {page_number} ({reply_count} found)...",
                    value
                )

        replies = _fetch_replies(
            c,
            session=session,
            lsd=lsd,
            jazoest=jazoest,
            page_callback=_reply_page_progress
        )

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
        if progress_callback:
            progress_callback("Done", 100)
        return empty_df, preview

    df = pd.DataFrame(rows)
    df.insert(0, "index", range(1, len(df) + 1))
    df = df[["index", "date", "author", "type", "comment", "like"]]

    if progress_callback:
        progress_callback("Done", 100)

    return df, preview
