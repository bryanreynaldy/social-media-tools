import requests
import pandas as pd
import re
import time
import random
from datetime import datetime

from config import INSTAGRAM_COOKIE
from utils.preview import fetch_generic_preview


class InstagramScraper:
    def __init__(self, raw_cookie):
        self.session = requests.Session()

        self.cookie_dict = self._parse_cookie(raw_cookie)
        self.session.cookies.update(self.cookie_dict)

        self.csrf_token = self.cookie_dict.get("csrftoken", "")

        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "X-IG-App-ID": "936619743392459",
            "X-ASBD-ID": "129477",
            "X-CSRFToken": self.csrf_token,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://www.instagram.com/",
            "Sec-Ch-Ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }
        self.session.headers.update(self.headers)

    def _parse_cookie(self, cookie_str):
        cookie_dict = {}
        for item in cookie_str.split(';'):
            if '=' in item:
                key, val = item.split('=', 1)
                cookie_dict[key.strip()] = val.strip()
        return cookie_dict

    def _shortcode_to_media_id(self, shortcode):
        alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'
        media_id = 0
        for char in shortcode:
            media_id = (media_id * 64) + alphabet.index(char)
        return str(media_id)

    def _format_date(self, ts):
        try:
            return datetime.fromtimestamp(ts).strftime('%b %d, %Y')
        except Exception:
            return None

    def _clean_comment(self, text):
        if text is None:
            return ""
        if not isinstance(text, str):
            text = str(text)

        text = text.replace("\r", " ").replace("\n", " ")
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def fetch_all_comments(self, post_url, max_parents=9000, progress_callback=None):

        if progress_callback:
            progress_callback("Finding post ID...", 10)

        try:
            shortcode = re.search(r"/(?:p|reels|reel)/([^/?#&]+)", post_url).group(1)
        except AttributeError:
            return pd.DataFrame(columns=["index", "date", "author", "type", "comment", "like"])

        media_id = self._shortcode_to_media_id(shortcode)

        all_comments = []
        next_min_id = ""

        url = f"https://www.instagram.com/api/v1/media/{media_id}/comments/"

        parent_processed = 0

        while len([x for x in all_comments if x.get("type") == "parent"]) < max_parents:

            params = {"can_support_threading": "true"}

            if next_min_id:
                params["min_id"] = next_min_id

            try:
                resp = self.session.get(url, params=params, timeout=15)

                if resp.status_code != 200:
                    break

                data = resp.json()

            except Exception:
                break

            comments = data.get("comments", [])

            if not comments:
                break

            if progress_callback:
                progress_callback("Extracting parent comments...", 30)

            for c in comments:

                parent_count_now = len([x for x in all_comments if x.get("type") == "parent"])

                if parent_count_now >= max_parents:
                    break

                user = c.get("user", {})
                comment_id = str(c.get("pk"))

                reply_count = c.get("child_comment_count", 0)
                timestamp = c.get("created_at")

                all_comments.append({
                    "date": self._format_date(timestamp),
                    "author": user.get("username") or "",
                    "type": "parent",
                    "comment": self._clean_comment(c.get("text", "")),
                    "like": c.get("comment_like_count", 0) or 0,
                    "_id": comment_id,
                    "_reply_count": reply_count
                })

                parent_processed += 1

                if progress_callback and parent_processed % 10 == 0:
                    progress = min(85, 30 + parent_processed)
                    progress_callback(f"Processing thread {parent_processed}...", progress)

                if reply_count > 0:

                    if progress_callback:
                        progress_callback(
                            f"Fetching replies for thread {parent_processed}...",
                            min(90, 40 + parent_processed)
                        )

                    replies = self._fetch_replies(media_id, comment_id)

                    all_comments.extend(replies)

                    time.sleep(random.uniform(0.8, 1.5))

            next_min_id = data.get("next_min_id")

            if not next_min_id:
                break

            time.sleep(random.uniform(1.5, 3.5))

        if progress_callback:
            progress_callback("Formatting results...", 95)

        df = pd.DataFrame(all_comments)

        if df.empty:
            return pd.DataFrame(columns=["index", "date", "author", "type", "comment", "like"])

        # ===============================
        # REMOVE DUPLICATES
        # ===============================

        df = df.drop(columns=["_id", "_reply_count"], errors="ignore")

        df = df.drop_duplicates(
            subset=["date", "author", "type", "comment", "like"]
        )

        df = df.reset_index(drop=True)

        # ===============================
        # FINAL FORMAT
        # ===============================

        df.insert(0, "index", range(1, len(df) + 1))

        return df


    def _fetch_replies(self, media_id, parent_comment_id):

        url = f"https://www.instagram.com/api/v1/media/{media_id}/comments/{parent_comment_id}/child_comments/"

        all_replies = []

        next_cursor = ""

        while True:

            params = {"max_id": next_cursor} if next_cursor else {}

            try:
                resp = self.session.get(url, params=params, timeout=10)

                if resp.status_code != 200:
                    break

                data = resp.json()

                for c in data.get("child_comments", []):

                    user = c.get("user", {})
                    timestamp = c.get("created_at")

                    all_replies.append({
                        "date": self._format_date(timestamp),
                        "author": user.get("username") or "",
                        "type": "reply",
                        "comment": self._clean_comment(c.get("text", "")),
                        "like": c.get("comment_like_count", 0) or 0,
                    })

                next_cursor = data.get("next_max_child_cursor")

                if not next_cursor:
                    break

                time.sleep(random.uniform(0.5, 1.0))

            except Exception:
                break

        return all_replies


def extract_instagram_comments(url: str, progress_callback=None, with_preview=False):

    preview = fetch_generic_preview(url) if with_preview else {}

    if preview:

        title = (preview.get("title") or "").strip()
        description = (preview.get("description") or "").strip()

        caption = description if description and description != title else title

        preview = {
            "title": caption,
            "description": None,
            "image": preview.get("image"),
            "author": preview.get("author") or "Instagram",
            "engagement": None,
            "mode": "instagram",
        }

    scraper = InstagramScraper(INSTAGRAM_COOKIE)

    df = scraper.fetch_all_comments(
        url,
        max_parents=9000,
        progress_callback=progress_callback
    )

    if progress_callback:
        progress_callback("Done", 100)

    return df, preview