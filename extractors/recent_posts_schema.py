import pandas as pd


STANDARD_POST_COLUMNS = [
    "index",
    "platform",
    "content_id",
    "content_code",
    "content_url",
    "content_type",
    "title",
    "caption",
    "description",
    "published_at_utc",
    "published_date_wib",
    "published_time_wib",
    "author_id",
    "author_username",
    "author_name",
    "audience_count",
    "view_count",
    "like_count",
    "comment_count",
    "engagement_count",
    "engagement_rate_pct",
    "duration",
    "duration_seconds",
    "is_short_form",
    "thumbnail_url",
    "image_url",
    "media_url",
    "mentions",
    "tagged_users",
    "tags",
    "has_mentions",
    "has_tagged_users",
    "text_character_count",
    "is_branded",
    "is_collaboration",
    "estimated_impressions",
    "live_actual_start_utc",
    "live_actual_end_utc",
    "live_scheduled_start_utc",
    "live_concurrent_viewers",
    "source_query",
]


INTEGER_COLUMNS = [
    "audience_count",
    "view_count",
    "like_count",
    "comment_count",
    "engagement_count",
    "duration_seconds",
    "text_character_count",
    "estimated_impressions",
    "live_concurrent_viewers",
]

BOOLEAN_COLUMNS = [
    "is_short_form",
    "has_mentions",
    "has_tagged_users",
    "is_branded",
    "is_collaboration",
]

FLOAT_COLUMNS = [
    "engagement_rate_pct",
]


def engagement_values(likes, comments, audience):
    if likes is None or comments is None:
        return None, None

    engagement = int(likes) + int(comments)
    if audience in (None, 0):
        return engagement, None

    return engagement, round((engagement / int(audience)) * 100, 4)


def to_standard_posts_dataframe(records) -> pd.DataFrame:
    df = pd.DataFrame(records)

    for column in STANDARD_POST_COLUMNS:
        if column not in df.columns:
            df[column] = pd.NA

    if df.empty:
        return df[STANDARD_POST_COLUMNS]

    df = df.drop_duplicates(subset=["platform", "content_id", "content_code"])
    df = df.reset_index(drop=True)
    df["index"] = range(1, len(df) + 1)

    for column in INTEGER_COLUMNS:
        df[column] = (
            pd.to_numeric(df[column], errors="coerce")
            .fillna(0)
            .astype("int64")
        )

    for column in FLOAT_COLUMNS:
        df[column] = (
            pd.to_numeric(df[column], errors="coerce")
            .fillna(0.0)
            .round(4)
        )

    for column in BOOLEAN_COLUMNS:
        df[column] = df[column].astype("boolean").fillna(False).astype(bool)

    typed_columns = set(INTEGER_COLUMNS + FLOAT_COLUMNS + BOOLEAN_COLUMNS + ["index"])
    text_columns = [
        column for column in STANDARD_POST_COLUMNS if column not in typed_columns
    ]
    for column in text_columns:
        df[column] = df[column].fillna("-").replace("", "-").astype(str)

    return df[STANDARD_POST_COLUMNS]
