import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from extractors.recent_posts_schema import (
    engagement_values,
    to_standard_posts_dataframe,
)


WIB = timezone(timedelta(hours=7))
CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")


def _api_execute(request, attempts=6):
    delay = 1
    for attempt in range(attempts):
        try:
            return request.execute()
        except HttpError as error:
            status = getattr(error.resp, "status", None)
            text = str(error).lower()
            retryable = (
                status in {429, 500, 502, 503, 504}
                or "ratelimitexceeded" in text
                or "backenderror" in text
            )
            if retryable and attempt < attempts - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise


def _chunked(values, size=50):
    for index in range(0, len(values), size):
        yield values[index:index + size]


def _safe_int(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _clean_text(value):
    return re.sub(r"\s+", " ", value or "").strip()


def _parse_duration_seconds(value):
    if not value:
        return None
    match = re.fullmatch(
        r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?",
        value,
    )
    if not match:
        return None
    days, hours, minutes, seconds = [int(part or 0) for part in match.groups()]
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _to_wib_parts(value):
    if not value:
        return None, None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(WIB)
    return parsed.strftime("%b %d, %Y"), parsed.strftime("%H:%M")


def _extract_channel_reference(value):
    value = (value or "").strip()
    if CHANNEL_ID_RE.fullmatch(value):
        return "id", value

    if value.startswith(("http://", "https://")):
        path = urlparse(value).path.strip("/")
        parts = path.split("/") if path else []
        first = parts[0] if parts else ""
        second = parts[1] if len(parts) > 1 else ""

        if first == "channel" and CHANNEL_ID_RE.fullmatch(second):
            return "id", second
        if first.startswith("@"):
            return "handle", first.lstrip("@")
        if first == "user" and second:
            return "username", second
        if first == "c" and second:
            return "search", second

    if value.startswith("@"):
        return "handle", value.lstrip("@")

    return "search", value


def _resolve_channel_id(youtube, value):
    kind, reference = _extract_channel_reference(value)

    if kind == "id":
        return reference

    if kind == "handle":
        response = _api_execute(youtube.channels().list(part="id", forHandle=reference))
        items = response.get("items", [])
        if items:
            return items[0]["id"]

    if kind == "username":
        response = _api_execute(youtube.channels().list(part="id", forUsername=reference))
        items = response.get("items", [])
        if items:
            return items[0]["id"]

    response = _api_execute(youtube.search().list(
        part="snippet",
        q=reference,
        type="channel",
        maxResults=1,
    ))
    items = response.get("items", [])
    if items:
        return items[0]["snippet"]["channelId"]

    raise ValueError("YouTube channel not found.")


def _get_channel_profile(youtube, channel_input):
    channel_id = _resolve_channel_id(youtube, channel_input)
    response = _api_execute(youtube.channels().list(
        part="snippet,statistics,contentDetails",
        id=channel_id,
    ))
    items = response.get("items", [])
    if not items:
        raise ValueError("YouTube channel data not found.")

    item = items[0]
    snippet = item.get("snippet", {})
    return {
        "channel_id": channel_id,
        "channel_title": snippet.get("title") or "",
        "channel_handle": snippet.get("customUrl") or "",
        "channel_subscribers": _safe_int(item.get("statistics", {}).get("subscriberCount")),
        "uploads_playlist_id": item.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads"),
    }


def _fetch_video_details(youtube, video_ids):
    details = {}
    for batch in _chunked(list(dict.fromkeys(video_ids)), 50):
        response = _api_execute(youtube.videos().list(
            part="snippet,statistics,contentDetails,liveStreamingDetails",
            id=",".join(batch),
        ))
        for item in response.get("items", []):
            details[item["id"]] = item
    return details


def _best_thumbnail(snippet):
    thumbnails = snippet.get("thumbnails", {}) or {}
    for key in ("maxres", "standard", "high", "medium", "default"):
        url = (thumbnails.get(key) or {}).get("url")
        if url:
            return url
    return ""


def fetch_recent_youtube_posts(
    channel_input,
    api_key,
    mode="all",
    limit=100,
    progress_callback=None,
):
    channel_input = (channel_input or "").strip()
    api_key = (api_key or "").strip()
    mode = (mode or "all").lower()

    if not channel_input:
        raise ValueError("Please enter a YouTube channel.")
    if not api_key:
        raise RuntimeError("YOUTUBE_API_KEY is not configured.")
    if mode not in {"all", "videos", "shorts"}:
        raise ValueError("Content mode must be all, videos, or shorts.")

    if progress_callback:
        progress_callback("Connecting to YouTube...", 6)
    youtube = build("youtube", "v3", developerKey=api_key, cache_discovery=False)

    if progress_callback:
        progress_callback("Finding channel...", 14)
    profile = _get_channel_profile(youtube, channel_input)
    playlist_id = profile.get("uploads_playlist_id")
    if not playlist_id:
        raise RuntimeError("The channel uploads playlist is unavailable.")

    selected = []
    page_token = None

    while len(selected) < limit:
        if progress_callback:
            progress_callback(
                f"Fetching posts · {len(selected):,}/{limit:,}",
                min(88, 22 + int((len(selected) / max(limit, 1)) * 66)),
            )

        request_args = {
            "part": "snippet,contentDetails",
            "playlistId": playlist_id,
            "maxResults": 50,
        }
        if page_token:
            request_args["pageToken"] = page_token

        response = _api_execute(youtube.playlistItems().list(**request_args))
        playlist_items = response.get("items", [])
        if not playlist_items:
            break

        video_ids = [
            item.get("contentDetails", {}).get("videoId")
            for item in playlist_items
            if item.get("contentDetails", {}).get("videoId")
        ]
        details = _fetch_video_details(youtube, video_ids)

        for playlist_item in playlist_items:
            content_details = playlist_item.get("contentDetails", {})
            playlist_snippet = playlist_item.get("snippet", {})
            video_id = content_details.get("videoId")
            detail = details.get(video_id)
            if not detail:
                continue

            snippet = detail.get("snippet", {})
            statistics = detail.get("statistics", {})
            video_content = detail.get("contentDetails", {})
            live = detail.get("liveStreamingDetails", {})

            duration = video_content.get("duration")
            duration_seconds = _parse_duration_seconds(duration)
            short_candidate = duration_seconds is not None and duration_seconds <= 180

            if mode == "videos" and (duration_seconds is None or short_candidate):
                continue
            if mode == "shorts" and not short_candidate:
                continue

            published_at = (
                snippet.get("publishedAt")
                or content_details.get("videoPublishedAt")
                or playlist_snippet.get("publishedAt")
            )
            date_wib, time_wib = _to_wib_parts(published_at)
            likes = _safe_int(statistics.get("likeCount"))
            comments = _safe_int(statistics.get("commentCount"))
            audience = profile.get("channel_subscribers")
            engagement, engagement_rate = engagement_values(likes, comments, audience)
            description = _clean_text(snippet.get("description") or playlist_snippet.get("description"))
            title = _clean_text(snippet.get("title") or playlist_snippet.get("title"))
            mentions = list(dict.fromkeys(re.findall(r"@([A-Za-z0-9_.-]+)", f"{title} {description}")))
            tags = snippet.get("tags", []) or []
            thumbnail = _best_thumbnail(snippet or playlist_snippet)
            is_live = any([
                live.get("actualStartTime"),
                live.get("scheduledStartTime"),
                live.get("actualEndTime"),
            ])

            selected.append({
                "platform": "YouTube",
                "content_id": video_id,
                "content_code": video_id,
                "content_url": f"https://youtu.be/{video_id}",
                "content_type": "live" if is_live else ("short" if short_candidate else "video"),
                "title": title,
                "caption": None,
                "description": description,
                "published_at_utc": published_at,
                "published_date_wib": date_wib,
                "published_time_wib": time_wib,
                "author_id": profile.get("channel_id"),
                "author_username": profile.get("channel_handle"),
                "author_name": profile.get("channel_title"),
                "audience_count": audience,
                "view_count": _safe_int(statistics.get("viewCount")),
                "like_count": likes,
                "comment_count": comments,
                "engagement_count": engagement,
                "engagement_rate_pct": engagement_rate,
                "duration": duration,
                "duration_seconds": duration_seconds,
                "is_short_form": short_candidate,
                "thumbnail_url": thumbnail,
                "image_url": thumbnail,
                "media_url": None,
                "mentions": ", ".join(mentions),
                "tagged_users": None,
                "tags": ", ".join(tags),
                "has_mentions": bool(mentions),
                "has_tagged_users": None,
                "text_character_count": len(description),
                "is_branded": None,
                "is_collaboration": None,
                "estimated_impressions": None,
                "live_actual_start_utc": live.get("actualStartTime"),
                "live_actual_end_utc": live.get("actualEndTime"),
                "live_scheduled_start_utc": live.get("scheduledStartTime"),
                "live_concurrent_viewers": _safe_int(live.get("concurrentViewers")),
                "source_query": channel_input,
            })

            if len(selected) >= limit:
                break

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    if progress_callback:
        progress_callback("Preparing standardized output...", 95)
    df = to_standard_posts_dataframe(selected)

    if not df.empty:
        sort_key = df["published_at_utc"].apply(
            lambda value: value if isinstance(value, str) else ""
        )
        df = df.assign(_published_sort=sort_key).sort_values(
            "_published_sort", ascending=False
        ).drop(columns="_published_sort").reset_index(drop=True)
        df["index"] = range(1, len(df) + 1)

    if progress_callback:
        progress_callback("Done", 100)
    return df
