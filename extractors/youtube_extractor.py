import re
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone, timedelta
from typing import Optional
import streamlit as st
import pandas as pd
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

YOUTUBE_API_KEY = st.secrets["YOUTUBE_API_KEY"]

def _ensure_youtube_client():
    return build("youtube", "v3", developerKey=YOUTUBE_API_KEY)


def _extract_video_id(url: str) -> str:
    url = url.strip()
    if not url:
        raise ValueError("Empty URL")

    p = urlparse(url)

    if p.netloc in {"youtu.be"}:
        vid = p.path.lstrip("/")
        if vid:
            return vid

    if p.path.startswith("/shorts/"):
        parts = p.path.split("/")
        if len(parts) >= 3 and parts[2]:
            return parts[2]

    if p.path == "/watch":
        qs = parse_qs(p.query)
        if "v" in qs and qs["v"]:
            return qs["v"][0]

    if p.path.startswith("/embed/"):
        parts = p.path.split("/")
        if len(parts) >= 3 and parts[2]:
            return parts[2]

    last = p.path.rstrip("/").split("/")[-1]
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", last):
        return last

    raise ValueError(f"Could not extract video ID from: {url}")


def _to_wib_date(rfc3339: Optional[str]):
    if not rfc3339:
        return None
    dt_utc = datetime.fromisoformat(rfc3339.replace("Z", "+00:00"))
    dt_wib = dt_utc.astimezone(timezone(timedelta(hours=7)))
    return dt_wib.strftime("%b %d, %Y")


def _clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _iter_comment_threads(youtube, video_id: str, order: str = "time"):
    page = None
    while True:
        kwargs = {
            "part": "snippet,replies",
            "videoId": video_id,
            "maxResults": 100,
            "order": order,
        }
        if page:
            kwargs["pageToken"] = page

        resp = youtube.commentThreads().list(**kwargs).execute()
        items = resp.get("items", [])
        for it in items:
            yield it

        page = resp.get("nextPageToken")
        if not page:
            break


def _iter_all_replies(youtube, parent_comment_id: str):
    page = None
    while True:
        kwargs = {
            "part": "snippet",
            "parentId": parent_comment_id,
            "maxResults": 100,
        }
        if page:
            kwargs["pageToken"] = page

        resp = youtube.comments().list(**kwargs).execute()
        items = resp.get("items", [])
        for c in items:
            yield c

        page = resp.get("nextPageToken")
        if not page:
            break


def _fetch_youtube_preview(youtube, video_id: str) -> dict:
    """
    Preview YouTube diambil dari API, bukan dari og meta,
    supaya author menjadi nama channel, bukan 'YouTube'.
    """
    try:
        resp = youtube.videos().list(
            part="snippet,statistics",
            id=video_id
        ).execute()

        items = resp.get("items", [])
        if not items:
            return {}

        item = items[0]
        snippet = item.get("snippet", {}) or {}
        statistics = item.get("statistics", {}) or {}
        thumbnails = snippet.get("thumbnails", {}) or {}

        image = None
        for key in ["maxres", "standard", "high", "medium", "default"]:
            thumb = thumbnails.get(key)
            if thumb and thumb.get("url"):
                image = thumb["url"]
                break

        like_count = statistics.get("likeCount")
        comment_count = statistics.get("commentCount")
        view_count = statistics.get("viewCount")

        parts = []
        if view_count is not None:
            parts.append(f"👁️ {view_count}")
        if like_count is not None:
            parts.append(f"👍 {like_count}")
        if comment_count is not None:
            parts.append(f"💬 {comment_count}")

        engagement = " | ".join(parts) if parts else None

        return {
            "title": snippet.get("title"),
            "description": snippet.get("description"),
            "image": image,
            "author": snippet.get("channelTitle"),
            "engagement": engagement,
            "mode": "youtube",
        }

    except Exception:
        return {
            "title": None,
            "description": None,
            "image": None,
            "author": None,
            "engagement": None,
            "mode": "youtube",
        }


def extract_youtube_comments(url: str, progress_callback=None, with_preview=False):
    # =========================
    # STEP 1: FIND VIDEO ID
    # =========================
    if progress_callback:
        progress_callback("Finding video ID...", 10)

    youtube = _ensure_youtube_client()
    video_id = _extract_video_id(url)

    preview = _fetch_youtube_preview(youtube, video_id) if with_preview else {}

    rows = []

    try:
        # =========================
        # STEP 2: FETCH COMMENTS
        # =========================
        if progress_callback:
            progress_callback("Extracting comments...", 30)

        thread_counter = 0

        for thread in _iter_comment_threads(youtube, video_id, order="time"):
            thread_counter += 1

            if progress_callback and thread_counter % 10 == 0:
                progress = min(80, 30 + thread_counter)
                progress_callback(f"Processing comment thread {thread_counter}...", progress)

            # -------------------------
            # TOP LEVEL COMMENT
            # -------------------------
            top = thread["snippet"]["topLevelComment"]
            ts = top["snippet"]
            top_id = top["id"]

            author_name = (ts.get("authorDisplayName") or "").lstrip("@")
            clean_text = _clean_text(ts.get("textOriginal") or ts.get("textDisplay"))
            date_val = _to_wib_date(ts.get("publishedAt"))

            rows.append({
                "date": date_val,
                "author": author_name if author_name else (ts.get("authorDisplayName") or ""),
                "type": "parent",
                "comment": clean_text,
                "like": ts.get("likeCount", 0),
            })

            # -------------------------
            # BUNDLED REPLIES
            # -------------------------
            bundled = (thread.get("replies", {}) or {}).get("comments", []) or []
            bundled_ids = set()

            for rep in bundled:
                rs = rep["snippet"]
                bundled_ids.add(rep["id"])

                author_name = (rs.get("authorDisplayName") or "").lstrip("@")
                clean_text = _clean_text(rs.get("textOriginal") or rs.get("textDisplay"))
                date_val = _to_wib_date(rs.get("publishedAt"))

                rows.append({
                    "date": date_val,
                    "author": author_name if author_name else (rs.get("authorDisplayName") or ""),
                    "type": "reply",
                    "comment": clean_text,
                    "like": rs.get("likeCount", 0),
                })

            # -------------------------
            # EXTRA REPLIES (pagination)
            # -------------------------
            total_reply_count = thread["snippet"].get("totalReplyCount", 0)
            remaining = max(total_reply_count - len(bundled), 0)

            if remaining:
                if progress_callback:
                    progress_callback("Fetching additional replies...", 60)

                for rep in _iter_all_replies(youtube, top_id):
                    if rep["id"] in bundled_ids:
                        continue

                    rs = rep["snippet"]

                    author_name = (rs.get("authorDisplayName") or "").lstrip("@")
                    clean_text = _clean_text(rs.get("textOriginal") or rs.get("textDisplay"))
                    date_val = _to_wib_date(rs.get("publishedAt"))

                    rows.append({
                        "date": date_val,
                        "author": author_name if author_name else (rs.get("authorDisplayName") or ""),
                        "type": "reply",
                        "comment": clean_text,
                        "like": rs.get("likeCount", 0),
                    })

    except HttpError as e:
        raise RuntimeError(f"YouTube API error: {e}")

    # =========================
    # STEP 3: FORMAT RESULTS
    # =========================
    if progress_callback:
        progress_callback("Formatting results...", 90)

    if not rows:
        empty_df = pd.DataFrame(
            columns=["index", "date", "author", "type", "comment", "like"]
        )
        return empty_df, preview

    df = pd.DataFrame(rows)
    df.insert(0, "index", range(1, len(df) + 1))
    df = df[["index", "date", "author", "type", "comment", "like"]]

    # =========================
    # DONE
    # =========================
    if progress_callback:
        progress_callback("Done", 100)

    return df, preview
