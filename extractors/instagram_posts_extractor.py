import json
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

import requests

from extractors.instagram_following_extractor import (
    is_valid_username,
    normalize_username,
)
from extractors.recent_posts_schema import (
    engagement_values,
    to_standard_posts_dataframe,
)


WEB_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
MOBILE_USER_AGENT = "Instagram 155.0.0.37.107"
IG_APP_ID = "936619743392459"
WIB = timezone(timedelta(hours=7))


def _parse_cookie_string(cookie_string):
    cookies = {}
    for part in (cookie_string or "").split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            key, value = part.split("=", 1)
            cookies[key.strip()] = unquote(value.strip())
        else:
            cookies[part] = ""
    return cookies


def _make_session(cookies):
    session = requests.Session()
    for key, value in cookies.items():
        session.cookies.set(key, value, domain=".instagram.com")

    session.headers.update({
        "User-Agent": WEB_USER_AGENT,
        "Accept": "*/*",
        "Referer": "https://www.instagram.com/",
        "X-Requested-With": "XMLHttpRequest",
    })
    return session


def _clean_caption(caption):
    return re.sub(r"\s+", " ", caption or "").strip()


def _timestamp_parts(timestamp):
    try:
        parsed_utc = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
        parsed_wib = parsed_utc.astimezone(WIB)
        return (
            parsed_utc.isoformat().replace("+00:00", "Z"),
            parsed_wib.strftime("%b %d, %Y"),
            parsed_wib.strftime("%H:%M"),
        )
    except (TypeError, ValueError, OSError):
        return None, None, None


def _extract_mentions(caption):
    return list(dict.fromkeys(re.findall(r"@([A-Za-z0-9_.]+)", caption or "")))


def _extract_tags(caption):
    return list(dict.fromkeys(re.findall(r"#([A-Za-z0-9_]+)", caption or "")))


def _detect_branded(caption, media_data=None):
    if media_data:
        if media_data.get("is_paid_partnership", False):
            return True
        branded_info = media_data.get("branded_content_tag_info", {})
        if branded_info and branded_info.get("sponsor_user"):
            return True

    keywords = [
        "#ad", "#sponsored", "#paid", "#paidpartnership", "#paidpromotion",
        "#collab", "#gifted", "#ambassador", "#brandambassador", "#partner",
        "#sp", "#spon", "paid partnership", "sponsored by",
        "in partnership with", "collaboration with", "gifted by",
        "#endorsement", "#iklan", "#promosi", "#kerjasamaberbayar",
    ]
    normalized = (caption or "").lower()
    return any(keyword in normalized for keyword in keywords)


def _detect_collaboration(media_data, target_username="", method="graphql"):
    if not media_data:
        return False

    if any(media_data.get(key) for key in (
        "coauthor_producers",
        "invited_coauthor_producers",
        "collaboration_info",
    )):
        return True

    owner_key = "owner" if method == "graphql" else "user"
    owner_username = (media_data.get(owner_key) or {}).get("username", "")
    if target_username and owner_username and owner_username.lower() != target_username.lower():
        return True

    if method == "graphql":
        sponsor_edges = media_data.get("edge_media_to_sponsor_user", {}).get("edges", [])
        if sponsor_edges:
            return True

    return False


def _extract_coauthors(media_data, method="graphql"):
    coauthors = []
    for key in ("coauthor_producers", "invited_coauthor_producers"):
        for item in media_data.get(key, []) or []:
            username = item.get("username", "")
            if username and username not in coauthors:
                coauthors.append(username)

    if method == "graphql":
        for edge in media_data.get("edge_media_to_sponsor_user", {}).get("edges", []):
            username = edge.get("node", {}).get("username", "")
            if username and username not in coauthors:
                coauthors.append(username)
    return coauthors


def _estimate_impressions(followers, engagement):
    if followers is None or engagement is None:
        return None
    return round(followers * 0.043988 + engagement * 10.1625)


def _media_type_name(media_type):
    return {1: "photo", 2: "reel", 8: "carousel"}.get(media_type, "photo")


def _get_user_info(session, username):
    try:
        response = session.get(
            f"https://i.instagram.com/api/v1/users/{username}/usernameinfo/",
            headers={
                "User-Agent": MOBILE_USER_AGENT,
                "Accept": "*/*",
                "X-IG-App-ID": IG_APP_ID,
                "Referer": f"https://www.instagram.com/{username}/",
            },
            timeout=15,
        )
        if response.status_code == 200:
            user = response.json().get("user", {})
            if user.get("pk"):
                return {
                    "user_id": int(user["pk"]),
                    "full_name": user.get("full_name", ""),
                    "followers": user.get("follower_count"),
                    "username": user.get("username", username),
                }
    except (requests.RequestException, ValueError, TypeError):
        pass

    try:
        response = session.get(
            f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}",
            headers={
                "User-Agent": WEB_USER_AGENT,
                "X-IG-App-ID": IG_APP_ID,
                "Referer": f"https://www.instagram.com/{username}/",
            },
            timeout=15,
        )
        if response.status_code == 200:
            user = response.json().get("data", {}).get("user", {})
            if user and user.get("id"):
                return {
                    "user_id": int(user["id"]),
                    "full_name": user.get("full_name", ""),
                    "followers": user.get("edge_followed_by", {}).get("count"),
                    "username": user.get("username", username),
                }
    except (requests.RequestException, ValueError, TypeError):
        pass

    return None


def _graphql_media_urls(node):
    thumbnail = node.get("thumbnail_src", "") or node.get("display_url", "")
    image_url = node.get("display_url", "") or thumbnail
    media_url = node.get("video_url", "") if node.get("is_video") else ""
    return thumbnail, image_url, media_url


def _mobile_media_urls(item):
    thumbnail = ""
    image_url = ""
    media_url = ""

    candidates = item.get("image_versions2", {}).get("candidates", [])
    if candidates:
        image_url = candidates[0].get("url", "")
        thumbnail = candidates[-1].get("url", "") if len(candidates) > 1 else image_url

    carousel = item.get("carousel_media", [])
    if carousel and not image_url:
        candidates = carousel[0].get("image_versions2", {}).get("candidates", [])
        if candidates:
            image_url = candidates[0].get("url", "")
            thumbnail = candidates[-1].get("url", "") if len(candidates) > 1 else image_url

    video_versions = item.get("video_versions", [])
    if video_versions:
        media_url = video_versions[0].get("url", "")

    return thumbnail, image_url, media_url


def _graphql_tagged_users(node):
    tagged = []
    for edge in node.get("edge_media_to_tagged_user", {}).get("edges", []):
        username = edge.get("node", {}).get("user", {}).get("username", "")
        if username and username not in tagged:
            tagged.append(username)
    return tagged


def _mobile_tagged_users(item):
    tagged = []
    media_items = [item] + list(item.get("carousel_media", []) or [])
    for media_item in media_items:
        for tag in media_item.get("usertags", {}).get("in", []):
            username = tag.get("user", {}).get("username", "")
            if username and username not in tagged:
                tagged.append(username)
    return tagged


def _standard_instagram_record(
    *,
    post_id,
    shortcode,
    raw_caption,
    timestamp,
    media_type,
    likes,
    comments,
    views,
    owner_id,
    owner_username,
    user_info,
    thumbnail,
    image_url,
    media_url,
    tagged_users,
    media_data,
    method,
    source_query,
):
    caption = _clean_caption(raw_caption)
    mentions = _extract_mentions(raw_caption)
    tags = _extract_tags(raw_caption)
    for username in _extract_coauthors(media_data, method):
        if username not in tagged_users:
            tagged_users.append(username)

    published_at, published_date, published_time = _timestamp_parts(timestamp)
    followers = user_info.get("followers")
    engagement, engagement_rate = engagement_values(likes, comments, followers)
    type_name = _media_type_name(media_type)
    post_path = "reel" if type_name == "reel" else "p"

    return {
        "platform": "Instagram",
        "content_id": str(post_id or ""),
        "content_code": shortcode,
        "content_url": f"https://www.instagram.com/{post_path}/{shortcode}/",
        "content_type": type_name,
        "title": None,
        "caption": caption,
        "description": None,
        "published_at_utc": published_at,
        "published_date_wib": published_date,
        "published_time_wib": published_time,
        "author_id": str(owner_id or user_info.get("user_id") or ""),
        "author_username": owner_username or user_info.get("username", ""),
        "author_name": user_info.get("full_name", ""),
        "audience_count": followers,
        "view_count": views,
        "like_count": likes,
        "comment_count": comments,
        "engagement_count": engagement,
        "engagement_rate_pct": engagement_rate,
        "duration": None,
        "duration_seconds": None,
        "is_short_form": type_name == "reel",
        "thumbnail_url": thumbnail,
        "image_url": image_url,
        "media_url": media_url,
        "mentions": ", ".join(mentions),
        "tagged_users": ", ".join(tagged_users),
        "tags": ", ".join(tags),
        "has_mentions": bool(mentions),
        "has_tagged_users": bool(tagged_users),
        "text_character_count": len(caption),
        "is_branded": _detect_branded(raw_caption, media_data),
        "is_collaboration": _detect_collaboration(
            media_data,
            target_username=user_info.get("username", ""),
            method=method,
        ),
        "estimated_impressions": _estimate_impressions(followers, engagement),
        "live_actual_start_utc": None,
        "live_actual_end_utc": None,
        "live_scheduled_start_utc": None,
        "live_concurrent_viewers": None,
        "source_query": source_query,
    }


def _build_graphql_record(node, user_info, source_query):
    caption_edges = node.get("edge_media_to_caption", {}).get("edges", [])
    raw_caption = caption_edges[0].get("node", {}).get("text", "") if caption_edges else ""
    typename = node.get("__typename", "")
    media_type = {"GraphImage": 1, "GraphVideo": 2, "GraphSidecar": 8}.get(typename, 1)
    owner = node.get("owner", {})
    thumbnail, image_url, media_url = _graphql_media_urls(node)

    return _standard_instagram_record(
        post_id=node.get("id"),
        shortcode=node.get("shortcode", ""),
        raw_caption=raw_caption,
        timestamp=node.get("taken_at_timestamp"),
        media_type=media_type,
        likes=node.get("edge_media_preview_like", {}).get("count", 0),
        comments=node.get("edge_media_to_comment", {}).get("count", 0),
        views=node.get("video_view_count", 0) if node.get("is_video") else None,
        owner_id=owner.get("id"),
        owner_username=owner.get("username", ""),
        user_info=user_info,
        thumbnail=thumbnail,
        image_url=image_url,
        media_url=media_url,
        tagged_users=_graphql_tagged_users(node),
        media_data=node,
        method="graphql",
        source_query=source_query,
    )


def _build_mobile_record(item, user_info, source_query):
    caption_data = item.get("caption")
    raw_caption = caption_data.get("text", "") if isinstance(caption_data, dict) else ""
    owner = item.get("user", {})
    thumbnail, image_url, media_url = _mobile_media_urls(item)
    views = item.get("view_count") or item.get("play_count")

    return _standard_instagram_record(
        post_id=item.get("pk"),
        shortcode=item.get("code", ""),
        raw_caption=raw_caption,
        timestamp=item.get("taken_at"),
        media_type=item.get("media_type", 1),
        likes=item.get("like_count", 0),
        comments=item.get("comment_count", 0),
        views=views,
        owner_id=owner.get("pk"),
        owner_username=owner.get("username", ""),
        user_info=user_info,
        thumbnail=thumbnail,
        image_url=image_url,
        media_url=media_url,
        tagged_users=_mobile_tagged_users(item),
        media_data=item,
        method="mobile",
        source_query=source_query,
    )


def _fetch_graphql(session, user_info, source_query, limit, progress_callback=None):
    posts = []
    end_cursor = None
    has_next_page = True

    while len(posts) < limit and has_next_page:
        variables = {"id": str(user_info["user_id"]), "first": 50}
        if end_cursor:
            variables["after"] = end_cursor

        response = session.get(
            "https://www.instagram.com/graphql/query/",
            params={
                "query_hash": "42323d64886122307be10013ad2dcc44",
                "variables": json.dumps(variables),
            },
            headers={
                "User-Agent": WEB_USER_AGENT,
                "Accept": "*/*",
                "X-IG-App-ID": IG_APP_ID,
                "Referer": f"https://www.instagram.com/{user_info['username']}/",
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=20,
        )
        if response.status_code != 200:
            break

        text = response.text[9:] if response.text.startswith("for (;;);") else response.text
        data = json.loads(text)
        timeline = (
            data.get("data", {})
            .get("user", {})
            .get("edge_owner_to_timeline_media", {})
        )
        edges = timeline.get("edges", [])
        for edge in edges:
            if len(posts) >= limit:
                break
            posts.append(_build_graphql_record(edge.get("node", {}), user_info, source_query))

        if progress_callback:
            progress_callback(
                f"Fetching posts · {len(posts):,}/{limit:,}",
                min(88, 24 + int((len(posts) / max(limit, 1)) * 64)),
            )

        page_info = timeline.get("page_info", {})
        has_next_page = page_info.get("has_next_page", False)
        end_cursor = page_info.get("end_cursor")
        if not has_next_page or not edges:
            break
        time.sleep(1.5)

    return posts


def _fetch_mobile(session, user_info, source_query, limit, progress_callback=None):
    posts = []
    max_id = None

    while len(posts) < limit:
        params = {"count": 50, "exclude_comment": "true"}
        if max_id:
            params["max_id"] = max_id

        response = session.get(
            f"https://i.instagram.com/api/v1/feed/user/{user_info['user_id']}/",
            headers={
                "User-Agent": MOBILE_USER_AGENT,
                "Accept": "*/*",
                "X-IG-App-ID": IG_APP_ID,
            },
            params=params,
            timeout=20,
        )
        if response.status_code in (401, 403):
            raise RuntimeError("Instagram cookie is expired or does not have access.")
        if response.status_code != 200:
            break

        data = response.json()
        items = data.get("items", [])
        if not items:
            break

        for item in items:
            if len(posts) >= limit:
                break
            posts.append(_build_mobile_record(item, user_info, source_query))

        if progress_callback:
            progress_callback(
                f"Fetching posts · {len(posts):,}/{limit:,}",
                min(88, 24 + int((len(posts) / max(limit, 1)) * 64)),
            )

        if not data.get("more_available", False):
            break
        max_id = data.get("next_max_id")
        if not max_id:
            break
        time.sleep(1.5)

    return posts


def fetch_recent_instagram_posts(
    username,
    cookie_string,
    limit=100,
    progress_callback=None,
):
    username = normalize_username(username)
    if not is_valid_username(username):
        raise ValueError("Please enter a valid Instagram username.")

    cookies = _parse_cookie_string(cookie_string)
    if not cookies.get("sessionid"):
        raise RuntimeError("INSTAGRAM_COOKIE must contain a valid sessionid.")

    if progress_callback:
        progress_callback("Checking Instagram session...", 7)
    session = _make_session(cookies)

    if progress_callback:
        progress_callback("Finding profile...", 15)
    user_info = _get_user_info(session, username)
    if not user_info:
        raise RuntimeError("Profile not found. Check the username or Instagram cookie.")

    if progress_callback:
        progress_callback("Fetching posts...", 23)

    try:
        posts = _fetch_graphql(
            session,
            user_info,
            username,
            limit,
            progress_callback,
        )
    except (requests.RequestException, ValueError, TypeError, json.JSONDecodeError):
        posts = []

    if not posts:
        if progress_callback:
            progress_callback("Switching Instagram source...", 28)
        try:
            posts = _fetch_mobile(
                session,
                user_info,
                username,
                limit,
                progress_callback,
            )
        except requests.RequestException as error:
            raise RuntimeError("Instagram could not be reached. Please try again.") from error

    if not posts:
        raise RuntimeError("No posts found. Check account access or Instagram rate limits.")

    if progress_callback:
        progress_callback("Preparing standardized output...", 95)
    df = to_standard_posts_dataframe(posts)

    if not df.empty:
        df = df.assign(
            _published_sort=df["published_at_utc"].fillna("").astype(str)
        ).sort_values("_published_sort", ascending=False).drop(
            columns="_published_sort"
        ).reset_index(drop=True)
        df["index"] = range(1, len(df) + 1)

    if progress_callback:
        progress_callback("Done", 100)
    return df
