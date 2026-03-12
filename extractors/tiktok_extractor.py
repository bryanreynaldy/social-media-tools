import re
import json
import time
import random
import requests
import pandas as pd
from datetime import datetime

from config import TIKTOK_COOKIE


class Tiktok:
    DEFAULT_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
    }

    SIGI_STATE_RE = re.compile(
        r'<script[^>]*id="SIGI_STATE"[^>]*>(.*?)</script>',
        re.DOTALL
    )
    NEXT_DATA_RE = re.compile(
        r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        re.DOTALL
    )
    UNIVERSAL_DATA_RE = re.compile(
        r'<script[^>]*id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
        re.DOTALL
    )

    def _parse_int(self, v):
        try:
            if v is None:
                return None
            if isinstance(v, (int, float)):
                return int(v)
            if isinstance(v, str):
                v = v.replace(",", "").strip()
                return int(v) if v.isdigit() else None
        except Exception:
            return None
        return None

    def _ts_to_formatted(self, ts):
        if not ts:
            return None
        try:
            try:
                from zoneinfo import ZoneInfo
                dt = datetime.fromtimestamp(int(ts), tz=ZoneInfo("Asia/Jakarta"))
            except Exception:
                dt = datetime.fromtimestamp(int(ts))
            return dt.strftime("%b %d, %Y")
        except Exception:
            return None

    def _clean_one_line(self, text):
        if text is None:
            return ""
        if not isinstance(text, str):
            text = str(text)

        text = text.replace("\\n", " ")
        text = text.replace("\r", " ").replace("\n", " ")
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _get_session(self, cookie: str):
        s = requests.Session()
        headers = self.DEFAULT_HEADERS.copy()
        headers.update({
            "Referer": "https://www.tiktok.com/",
            "Origin": "https://www.tiktok.com",
            "Connection": "keep-alive",
        })
        if cookie:
            headers["Cookie"] = cookie
        s.headers.update(headers)
        return s

    def _request_json(self, session: requests.Session, endpoint: str, params: dict,
                      timeout: int = 20, retries: int = 3, sleep_range=(1.0, 2.0)):
        last_err = None
        for i in range(retries):
            try:
                r = session.get(endpoint, params=params, timeout=timeout)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                last_err = e
                if i < retries - 1:
                    time.sleep(random.uniform(*sleep_range))
        raise RuntimeError(f"Request gagal setelah {retries} percobaan: {last_err}")

    def _extract_aweme_id_from_html(self, html: str):
        m = self.UNIVERSAL_DATA_RE.search(html)
        if m:
            try:
                data = json.loads(m.group(1))
                for scope_key in ("webapp.video-detail", "webapp.photo-detail"):
                    item = (
                        data.get("__DEFAULT_SCOPE__", {})
                        .get(scope_key, {})
                        .get("itemInfo", {})
                        .get("itemStruct", {})
                    )
                    if isinstance(item, dict) and item:
                        aweme_id = item.get("id")
                        if aweme_id:
                            return str(aweme_id)
            except Exception:
                pass

        m = self.SIGI_STATE_RE.search(html)
        if m:
            try:
                data = json.loads(m.group(1))
                item_module = data.get("ItemModule") or {}
                if isinstance(item_module, dict) and item_module:
                    first_key = next(iter(item_module.keys()), None)
                    if first_key:
                        return str(first_key)
                    first_item = next(iter(item_module.values()))
                    if isinstance(first_item, dict) and first_item.get("id"):
                        return str(first_item["id"])
            except Exception:
                pass

        m = self.NEXT_DATA_RE.search(html)
        if m:
            try:
                data = json.loads(m.group(1))
                item = (
                    data.get("props", {})
                    .get("pageProps", {})
                    .get("itemInfo", {})
                    .get("itemStruct", {})
                )
                if isinstance(item, dict) and item:
                    aweme_id = item.get("id")
                    if aweme_id:
                        return str(aweme_id)
            except Exception:
                pass

        return None

    def _extract_item_struct_from_html(self, html: str):
        m = self.UNIVERSAL_DATA_RE.search(html)
        if m:
            try:
                data = json.loads(m.group(1))
                default_scope = data.get("__DEFAULT_SCOPE__", {})

                for scope_key in ("webapp.video-detail", "webapp.photo-detail"):
                    item_struct = (
                        default_scope.get(scope_key, {})
                        .get("itemInfo", {})
                        .get("itemStruct", {})
                    )
                    if isinstance(item_struct, dict) and item_struct:
                        return item_struct
            except Exception:
                pass

        m = self.SIGI_STATE_RE.search(html)
        if m:
            try:
                data = json.loads(m.group(1))
                item_module = data.get("ItemModule") or {}
                if isinstance(item_module, dict) and item_module:
                    first_item = next(iter(item_module.values()))
                    if isinstance(first_item, dict) and first_item:
                        return first_item
            except Exception:
                pass

        m = self.NEXT_DATA_RE.search(html)
        if m:
            try:
                data = json.loads(m.group(1))
                item_struct = (
                    data.get("props", {})
                    .get("pageProps", {})
                    .get("itemInfo", {})
                    .get("itemStruct", {})
                )
                if isinstance(item_struct, dict) and item_struct:
                    return item_struct
            except Exception:
                pass

        return {}

    def _extract_cover_from_item_struct(self, item_struct: dict):
        video = item_struct.get("video") or {}
        if video.get("cover"):
            return video.get("cover")
        if video.get("originCover"):
            return video.get("originCover")
        if video.get("dynamicCover"):
            return video.get("dynamicCover")

        image_post = item_struct.get("imagePost") or {}
        if isinstance(image_post, dict):
            images = image_post.get("images") or []
            if images:
                first_img = images[0] or {}
                image_url = first_img.get("imageURL") or {}
                url_list = image_url.get("urlList") or []
                if url_list:
                    return url_list[0]

        return None

    def _fetch_preview(self, url: str, cookie: str):
        try:
            session = self._get_session(cookie)
            resp = session.get(url, timeout=20, allow_redirects=True)
            resp.raise_for_status()

            html = resp.text
            item_struct = self._extract_item_struct_from_html(html)

            if item_struct:
                author_info = item_struct.get("author") or {}
                stats = item_struct.get("stats") or {}

                author = (
                    author_info.get("nickname")
                    or author_info.get("uniqueId")
                    or author_info.get("unique_id")
                    or "TikTok"
                )

                caption = item_struct.get("desc") or None
                image = self._extract_cover_from_item_struct(item_struct)

                engagement = (
                    f"❤️ {stats.get('diggCount', 0)} | "
                    f"💬 {stats.get('commentCount', 0)} | "
                    f"↪️ {stats.get('shareCount', 0)}"
                )

                return {
                    "title": caption,
                    "description": None,
                    "image": image,
                    "author": author,
                    "engagement": engagement,
                    "mode": "tiktok",
                }

            return {
                "title": None,
                "description": None,
                "image": None,
                "author": "TikTok",
                "engagement": None,
                "mode": "tiktok",
            }

        except Exception:
            return {
                "title": None,
                "description": None,
                "image": None,
                "author": "TikTok",
                "engagement": None,
                "mode": "tiktok",
            }

    def _get_aweme_id_from_url(self, url: str, cookie: str, timeout: int = 20):
        url = url.replace("m.tiktok.com/", "www.tiktok.com/")
        if "/photo/" in url and "/video/" not in url:
            url = url.replace("/photo/", "/video/")

        session = self._get_session(cookie)
        resp = session.get(url, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()

        aweme_id = self._extract_aweme_id_from_html(resp.text)
        if not aweme_id:
            raise RuntimeError("Gagal menemukan aweme_id dari halaman.")
        return aweme_id

    def _normalize_comment(self, c: dict, aweme_id: str, parent_cid: str = None):
        user = (c or {}).get("user") or {}
        text = c.get("text")
        create_time = self._parse_int(c.get("create_time"))
        author = user.get("nickname") or user.get("unique_id") or user.get("uid") or ""

        return {
            "date": self._ts_to_formatted(create_time),
            "author": author,
            "type": "reply" if parent_cid is not None else "parent",
            "comment": self._clean_one_line(text),
            "like": self._parse_int(c.get("digg_count")) or 0,
            "_aweme_id": aweme_id,
            "_comment_id": str(c.get("cid")) if c.get("cid") is not None else None,
            "_reply_count": self._parse_int(c.get("reply_comment_total")) or 0,
        }

    def fetch_parent_comments(self, cookie: str, video_url: str,
                              count: int = 50, max_pages: int = 50, timeout: int = 20):
        aweme_id = self._get_aweme_id_from_url(video_url, cookie=cookie, timeout=timeout)
        session = self._get_session(cookie)

        endpoint = "https://www.tiktok.com/api/comment/list/"
        cursor = 0
        out = []

        for _ in range(max_pages):
            params = {
                "aweme_id": aweme_id,
                "count": count,
                "cursor": cursor,
                "aid": 1988,
            }
            data = self._request_json(session, endpoint, params=params, timeout=timeout)

            if isinstance(data, dict) and data.get("status_code") not in (0, None):
                raise RuntimeError(f"comment/list status_code={data.get('status_code')}")

            comments = data.get("comments") or []
            if not comments:
                break

            for c in comments:
                out.append(self._normalize_comment(c, aweme_id, parent_cid=None))

            has_more = bool(data.get("has_more"))
            cursor = data.get("cursor")
            if cursor is None or not has_more:
                break

            time.sleep(random.uniform(0.8, 1.6))

        return aweme_id, out

    def fetch_replies(self, cookie: str, aweme_id: str, parent_comment_id: str,
                      count: int = 50, max_pages: int = 50, timeout: int = 20):
        session = self._get_session(cookie)
        endpoint = "https://www.tiktok.com/api/comment/list/reply/"
        cursor = 0
        out = []

        for _ in range(max_pages):
            params = {
                "aweme_id": aweme_id,
                "comment_id": parent_comment_id,
                "count": count,
                "cursor": cursor,
                "aid": 1988,
            }
            data = self._request_json(session, endpoint, params=params, timeout=timeout)

            if isinstance(data, dict) and data.get("status_code") not in (0, None):
                raise RuntimeError(f"comment/list/reply status_code={data.get('status_code')}")

            comments = data.get("comments") or data.get("replies") or []
            if not comments:
                break

            for c in comments:
                out.append(self._normalize_comment(c, aweme_id, parent_cid=parent_comment_id))

            has_more = bool(data.get("has_more"))
            cursor = data.get("cursor")
            if cursor is None or not has_more:
                break

            time.sleep(random.uniform(0.8, 1.6))

        return out

    def fetch_comments_df(self, cookie: str, video_url: str,
                          count: int = 50, max_pages: int = 50,
                          reply_count: int = 50, reply_max_pages: int = 50,
                          timeout: int = 20, include_replies: bool = True,
                          progress_callback=None) -> pd.DataFrame:
        if progress_callback:
            progress_callback("Finding post ID...", 10)

        aweme_id, parents = self.fetch_parent_comments(
            cookie=cookie,
            video_url=video_url,
            count=count,
            max_pages=max_pages,
            timeout=timeout
        )

        if progress_callback:
            progress_callback("Extracting parent comments...", 30)

        rows = []
        total_parents = len(parents) if parents else 1

        for i, p in enumerate(parents, start=1):
            rows.append(p)

            if progress_callback:
                progress = min(85, 30 + int((i / total_parents) * 50))
                progress_callback(f"Processing thread {i}/{total_parents}...", progress)

            if include_replies:
                cid = p.get("_comment_id")
                rc = p.get("_reply_count") or 0

                if cid and rc > 0:
                    replies = self.fetch_replies(
                        cookie=cookie,
                        aweme_id=aweme_id,
                        parent_comment_id=cid,
                        count=reply_count,
                        max_pages=reply_max_pages,
                        timeout=timeout
                    )
                    rows.extend(replies)
                    time.sleep(random.uniform(0.6, 1.2))

        if progress_callback:
            progress_callback("Formatting results...", 95)

        if not rows:
            return pd.DataFrame(columns=["index", "date", "author", "type", "comment", "like"])

        df = pd.DataFrame(rows)
        df = df[["date", "author", "type", "comment", "like"]]
        df.insert(0, "index", range(1, len(df) + 1))
        return df


def extract_tiktok_comments(url: str, progress_callback=None, with_preview=False):
    tt = Tiktok()

    preview = tt._fetch_preview(url, TIKTOK_COOKIE) if with_preview else {}

    df = tt.fetch_comments_df(
        cookie=TIKTOK_COOKIE,
        video_url=url,
        count=50,
        max_pages=50,
        reply_count=50,
        reply_max_pages=50,
        timeout=20,
        include_replies=True,
        progress_callback=progress_callback
    )

    if progress_callback:
        progress_callback("Done", 100)

    return df, preview