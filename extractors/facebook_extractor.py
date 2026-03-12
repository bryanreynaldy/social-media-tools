import requests
import json
import re
import base64
import pandas as pd
from html import unescape
from datetime import datetime

from utils.preview import fetch_generic_preview

GRAPHQL = "https://www.facebook.com/api/graphql/"

BASE_HEADERS = {
    "user-agent": "Mozilla/5.0",
    "content-type": "application/x-www-form-urlencoded"
}


def _extract_post_id(url):
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    html = r.text

    og_url_match = re.search(r'<meta property="og:url" content="([^"]+)"', html)
    if not og_url_match:
        raise Exception("og:url tidak ditemukan. Link mungkin tidak valid atau post tidak public.")

    og_url = unescape(og_url_match.group(1))

    m = re.search(r'/posts/(?:[^/]+/)?(\d+)', og_url)
    if not m:
        m = re.search(r'story_fbid=(\d+)', og_url)

    if not m:
        raise Exception("Post ID tidak ditemukan dari URL.")

    return m.group(1)


def _convert_feedback_id(post_id):
    return base64.b64encode(f"feedback:{post_id}".encode()).decode()


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
        progress_callback("Finding post ID...", 10)

    post_id = _extract_post_id(url)
    feedback_id = _convert_feedback_id(post_id)

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