import base64
from io import BytesIO
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from extractors.instagram_following_extractor import normalize_username
from extractors.instagram_posts_extractor import fetch_recent_instagram_posts
from extractors.youtube_posts_extractor import fetch_recent_youtube_posts
from utils.loading import Loader


st.set_page_config(page_title="Fetch Last Posts", page_icon="🗂️", layout="wide")


def img_to_data_uri(path: str) -> str:
    file_path = Path(path)
    if not file_path.exists():
        return ""
    encoded = base64.b64encode(file_path.read_bytes()).decode()
    return f"data:image/png;base64,{encoded}"


def platform_label(logo_uri: str, name: str) -> str:
    return f"![{name}]({logo_uri}) {name}" if logo_uri else name


youtube_logo = img_to_data_uri("assets/youtube.png")
instagram_logo = img_to_data_uri("assets/instagram.png")


st.markdown("""
<style>
.stMainBlockContainer,
section.main > div.block-container {
    max-width: 1120px;
    padding-top: 2.25rem;
    padding-bottom: 4rem;
}

[data-testid="stRadioGroup"] {
    display: grid !important;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 14px !important;
    width: 100%;
}

[data-testid="stRadioOption"] {
    position: relative;
    display: flex !important;
    align-items: center;
    justify-content: center !important;
    min-height: 118px;
    padding: 16px 14px !important;
    border: 1px solid #42526a !important;
    border-radius: 16px !important;
    background: #273449 !important;
    cursor: pointer;
    transition: border-color .16s ease, background-color .16s ease, box-shadow .16s ease, transform .16s ease;
}

[data-testid="stRadioOption"]:hover {
    border-color: #7595bd !important;
    background: #30415a !important;
    box-shadow: 0 8px 22px rgba(30, 64, 175, .16);
    transform: translateY(-1px);
}

[data-testid="stRadioOption"]:has(input:checked) {
    border: 2px solid #8cc5ff !important;
    background: linear-gradient(145deg, #416a9d 0%, #315579 100%) !important;
    box-shadow: 0 0 0 3px rgba(96, 165, 250, .22), 0 14px 30px rgba(31, 73, 125, .28);
    transform: translateY(-3px);
}

[data-testid="stRadioOption"]:has(input:checked)::after {
    content: "✓";
    position: absolute;
    top: 10px;
    right: 10px;
    display: grid;
    place-items: center;
    width: 22px;
    height: 22px;
    border-radius: 999px;
    background: #2f6fcb;
    color: #ffffff;
    font-size: 12px;
    font-weight: 800;
}

[data-testid="stRadioOption"] > span,
[data-testid="stRadioOption"] input[type="radio"] {
    position: absolute !important;
    width: 1px !important;
    height: 1px !important;
    overflow: hidden !important;
    opacity: 0 !important;
}

[data-testid="stRadioOption"] [data-testid="stMarkdownContainer"] {
    width: 100% !important;
}

[data-testid="stRadioOption"] p {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 9px;
    margin: 0;
    color: #f8fafc;
    font-size: .98rem;
    font-weight: 700;
}

[data-testid="stRadioOption"] img {
    display: block !important;
    width: 56px !important;
    height: 56px !important;
    padding: 7px !important;
    border: 1px solid #d8dee8 !important;
    border-radius: 14px !important;
    background: #f8fafc;
    object-fit: contain !important;
    box-sizing: border-box;
}

[data-testid="stRadioOption"] img[alt="YouTube"] {
    padding: 14px 7px !important;
}

div[data-testid="stTextInput"] input,
div[data-testid="stNumberInput"] input,
div[data-testid="stSelectbox"] > div > div {
    min-height: 48px;
    border-radius: 11px;
}

div[data-testid="stButton"] button {
    min-height: 54px;
    border: 1px solid #2f6fcb;
    border-radius: 12px;
    background: #2f6fcb;
    color: #ffffff;
    font-weight: 700;
}

div[data-testid="stButton"] button:hover {
    border-color: #245cad;
    background: #245cad;
    color: #ffffff;
}

div[data-testid="stMetric"] {
    min-height: 112px;
    padding: 17px 18px;
    border: 1px solid #42526a;
    border-radius: 14px;
    background: #273449;
}

div[data-testid="stMetric"] [data-testid="stMetricLabel"],
div[data-testid="stMetric"] [data-testid="stMetricLabel"] p,
div[data-testid="stMetric"] [data-testid="stMetricValue"],
div[data-testid="stMetric"] [data-testid="stMetricValue"] > div {
    color: #f8fafc !important;
}

div[data-testid="stDownloadButton"] button {
    min-height: 46px;
    border-radius: 10px;
    font-weight: 650;
}

[data-testid="stVegaLiteChart"] {
    padding: 10px 8px 4px;
    border: 1px solid rgba(66, 82, 106, .65);
    border-radius: 14px;
}

[data-testid="stExpander"] {
    border-color: #42526a !important;
    border-radius: 12px !important;
}

@media (max-width: 640px) {
    .stMainBlockContainer,
    section.main > div.block-container {
        padding-top: 1.25rem;
    }

    [data-testid="stRadioGroup"] {
        gap: 9px !important;
    }

    [data-testid="stRadioOption"] {
        min-height: 104px;
        padding: 13px 10px !important;
    }

    [data-testid="stRadioOption"] img {
        width: 48px !important;
        height: 48px !important;
    }
}
</style>
""", unsafe_allow_html=True)


def _numeric_series(df, column):
    if column not in df.columns:
        return pd.Series(dtype="float64")
    return pd.to_numeric(df[column], errors="coerce").dropna()


def _formatted_sum(df, column):
    values = _numeric_series(df, column)
    return "—" if values.empty else f"{int(values.sum()):,}"


def _formatted_mean(df, column, suffix=""):
    values = _numeric_series(df, column)
    return "—" if values.empty else f"{values.mean():,.2f}{suffix}"


def _count_true(df, column):
    if column not in df.columns:
        return 0
    return int(df[column].fillna(False).astype(bool).sum())


def _format_duration(seconds):
    if seconds is None or pd.isna(seconds):
        return "—"
    seconds = int(seconds)
    minutes, remaining = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{remaining:02d}" if hours else f"{minutes}:{remaining:02d}"


def posts_download_buttons(df, file_name):
    csv_data = df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
    excel_data = BytesIO()
    with pd.ExcelWriter(excel_data, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)

    csv_col, excel_col = st.columns(2)
    with csv_col:
        st.download_button(
            "Download CSV",
            data=csv_data,
            file_name=f"{file_name}.csv",
            mime="text/csv",
            use_container_width=True,
            on_click="ignore",
        )
    with excel_col:
        st.download_button(
            "Download Excel",
            data=excel_data.getvalue(),
            file_name=f"{file_name}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            on_click="ignore",
        )


CHART_COLORS = ["#2f6fcb", "#8cc5ff", "#7595bd", "#4f77a8", "#a8c7ea", "#34506f"]


def _post_label(row, max_length=58):
    for column in ("title", "caption", "description", "content_code"):
        value = str(row.get(column, "")).strip()
        if value and value != "-":
            return value if len(value) <= max_length else f"{value[:max_length - 1]}…"
    return "Untitled post"


def _visualization_dataframe(df):
    chart_df = df.copy()
    chart_df["post_label"] = chart_df.apply(_post_label, axis=1)
    chart_df["published_dt"] = pd.to_datetime(
        chart_df["published_at_utc"].replace("-", pd.NA),
        errors="coerce",
        utc=True,
    )
    for column in (
        "view_count",
        "like_count",
        "comment_count",
        "engagement_count",
        "engagement_rate_pct",
    ):
        chart_df[column] = pd.to_numeric(chart_df[column], errors="coerce").fillna(0)
    return chart_df


def render_visualizations(df):
    chart_df = _visualization_dataframe(df)
    tooltip_fields = [
        alt.Tooltip("post_label:N", title="Post"),
        alt.Tooltip("published_date_wib:N", title="Published"),
        alt.Tooltip("content_type:N", title="Type"),
        alt.Tooltip("view_count:Q", title="Views", format=","),
        alt.Tooltip("like_count:Q", title="Likes", format=","),
        alt.Tooltip("comment_count:Q", title="Comments", format=","),
        alt.Tooltip("engagement_count:Q", title="Engagement", format=","),
        alt.Tooltip("engagement_rate_pct:Q", title="Rate", format=".2f"),
    ]

    st.subheader("Performance")

    dated_df = chart_df.dropna(subset=["published_dt"]).sort_values("published_dt")
    if not dated_df.empty:
        base = alt.Chart(dated_df).encode(
            x=alt.X(
                "published_dt:T",
                title="Published",
                axis=alt.Axis(format="%d %b", labelAngle=-35, tickCount=8),
            )
        )
        views = base.mark_bar(
            color=CHART_COLORS[1],
            opacity=0.42,
            cornerRadiusTopLeft=3,
            cornerRadiusTopRight=3,
        ).encode(
            y=alt.Y(
                "view_count:Q",
                title="Reported Views",
                axis=alt.Axis(titleColor=CHART_COLORS[1], format="~s"),
            ),
            tooltip=tooltip_fields,
        )
        engagement = base.mark_line(
            color=CHART_COLORS[0],
            strokeWidth=3,
            point=alt.OverlayMarkDef(filled=True, size=70),
        ).encode(
            y=alt.Y(
                "engagement_count:Q",
                title="Engagement",
                axis=alt.Axis(
                    titleColor=CHART_COLORS[0],
                    orient="right",
                    format="~s",
                ),
            ),
            tooltip=tooltip_fields,
        )
        trend_chart = alt.layer(views, engagement).resolve_scale(
            y="independent"
        ).properties(height=310)
        st.altair_chart(trend_chart, use_container_width=True)

    analysis_left, analysis_right = st.columns(2)

    with analysis_left:
        st.markdown("#### Content Mix")
        mix_df = (
            chart_df.groupby("content_type", as_index=False)
            .agg(posts=("content_id", "count"))
            .sort_values("posts", ascending=False)
        )
        mix_chart = alt.Chart(mix_df).mark_arc(
            innerRadius=62,
            outerRadius=106,
            strokeWidth=2,
        ).encode(
            theta=alt.Theta("posts:Q", stack=True),
            color=alt.Color(
                "content_type:N",
                title=None,
                scale=alt.Scale(range=CHART_COLORS),
                legend=alt.Legend(orient="bottom", columns=2),
            ),
            tooltip=[
                alt.Tooltip("content_type:N", title="Type"),
                alt.Tooltip("posts:Q", title="Posts", format=","),
            ],
        ).properties(height=290)
        st.altair_chart(mix_chart, use_container_width=True)

    with analysis_right:
        st.markdown("#### Engagement by Type")
        type_df = (
            chart_df.groupby("content_type", as_index=False)
            .agg(
                avg_engagement=("engagement_count", "mean"),
                avg_rate=("engagement_rate_pct", "mean"),
                posts=("content_id", "count"),
            )
            .sort_values("avg_engagement", ascending=False)
        )
        type_chart = alt.Chart(type_df).mark_bar(
            cornerRadiusEnd=5,
        ).encode(
            x=alt.X(
                "avg_engagement:Q",
                title="Average Engagement",
                axis=alt.Axis(format="~s"),
            ),
            y=alt.Y(
                "content_type:N",
                title=None,
                sort="-x",
                axis=alt.Axis(labelLimit=140),
            ),
            color=alt.Color(
                "content_type:N",
                title=None,
                scale=alt.Scale(range=CHART_COLORS),
                legend=None,
            ),
            tooltip=[
                alt.Tooltip("content_type:N", title="Type"),
                alt.Tooltip("posts:Q", title="Posts", format=","),
                alt.Tooltip("avg_engagement:Q", title="Avg. Engagement", format=",.1f"),
                alt.Tooltip("avg_rate:Q", title="Avg. Rate", format=".2f"),
            ],
        ).properties(height=290)
        st.altair_chart(type_chart, use_container_width=True)

    st.subheader("Top Posts")
    top_df = chart_df.nlargest(min(8, len(chart_df)), "engagement_count").copy()
    top_df = top_df.sort_values("engagement_count", ascending=True)
    top_chart = alt.Chart(top_df).mark_bar(
        cornerRadiusEnd=5,
    ).encode(
        x=alt.X(
            "engagement_count:Q",
            title="Engagement",
            axis=alt.Axis(format="~s"),
        ),
        y=alt.Y(
            "post_label:N",
            title=None,
            sort=alt.EncodingSortField(field="engagement_count", order="ascending"),
            axis=alt.Axis(labelLimit=300),
        ),
        color=alt.Color(
            "content_type:N",
            title="Type",
            scale=alt.Scale(range=CHART_COLORS),
            legend=alt.Legend(orient="bottom"),
        ),
        tooltip=tooltip_fields,
    ).properties(height=max(260, min(390, len(top_df) * 42)))
    st.altair_chart(top_chart, use_container_width=True)


def render_results(df, platform, target):
    audience_values = _numeric_series(df, "audience_count")
    audience = "—" if audience_values.empty else f"{int(audience_values.iloc[0]):,}"

    st.success(f"Complete — {len(df):,} posts found.")

    row1 = st.columns(4)
    row1[0].metric("Total Posts", f"{len(df):,}")
    row1[1].metric("Audience", audience)
    row1[2].metric("Reported Views", _formatted_sum(df, "view_count"))
    row1[3].metric("Likes", _formatted_sum(df, "like_count"))

    row2 = st.columns(4)
    row2[0].metric("Comments", _formatted_sum(df, "comment_count"))
    row2[1].metric("Engagement", _formatted_sum(df, "engagement_count"))
    row2[2].metric("Avg. Engagement", _formatted_mean(df, "engagement_count"))
    row2[3].metric("Avg. Rate", _formatted_mean(df, "engagement_rate_pct", "%"))

    st.subheader("Signals")
    signals = st.columns(4)
    if platform == "Instagram":
        signals[0].metric("Est. Impressions", _formatted_sum(df, "estimated_impressions"))
        signals[1].metric("Branded", f"{_count_true(df, 'is_branded'):,}")
        signals[2].metric("Collaborations", f"{_count_true(df, 'is_collaboration'):,}")
        signals[3].metric("Reels", f"{int((df['content_type'] == 'reel').sum()):,}")
    else:
        duration_values = _numeric_series(df, "duration_seconds")
        average_duration = duration_values.mean() if not duration_values.empty else None
        signals[0].metric("Shorts", f"{_count_true(df, 'is_short_form'):,}")
        signals[1].metric("Live", f"{int((df['content_type'] == 'live').sum()):,}")
        signals[2].metric("Avg. Duration", _format_duration(average_duration))
        signals[3].metric(
            "With Tags",
            f"{int(df['tags'].fillna('').astype(str).str.strip().ne('').sum()):,}",
        )

    render_visualizations(df)

    with st.expander("Data Preview · 41 columns"):
        st.dataframe(
            df.head(30),
            use_container_width=True,
            hide_index=True,
            column_config={
                "content_url": st.column_config.LinkColumn("content_url", display_text="Open"),
                "thumbnail_url": st.column_config.LinkColumn("thumbnail_url", display_text="Open"),
                "image_url": st.column_config.LinkColumn("image_url", display_text="Open"),
                "media_url": st.column_config.LinkColumn("media_url", display_text="Open"),
                "engagement_rate_pct": st.column_config.NumberColumn(
                    "engagement_rate_pct", format="%.4f%%"
                ),
            },
        )

    st.subheader("Download")
    safe_target = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in target.strip().lstrip("@")
    ).strip("_") or platform.lower()
    posts_download_buttons(df, f"{safe_target}_last_posts")


youtube_option = platform_label(youtube_logo, "YouTube")
instagram_option = platform_label(instagram_logo, "Instagram")

platform_option = st.radio(
    "Select Platform",
    [youtube_option, instagram_option],
    horizontal=True,
)
platform = "YouTube" if "YouTube" in platform_option else "Instagram"

if platform == "YouTube":
    target_label = "Channel"
    target_placeholder = "@handle, channel name, ID, or URL"
else:
    target_label = "Target Username"
    target_placeholder = "username"

input_col, limit_col = st.columns([3, 1])
with input_col:
    target_input = st.text_input(target_label, placeholder=target_placeholder).strip()
with limit_col:
    limit = int(st.number_input("Posts", min_value=1, max_value=150, value=50, step=1))

mode = "all"
if platform == "YouTube":
    mode_label = st.selectbox("Content", ["All", "Videos", "Shorts"])
    mode = mode_label.lower()

target = normalize_username(target_input) if platform == "Instagram" else target_input
request_state = {
    "platform": platform,
    "target": target,
    "limit": limit,
    "mode": mode,
}

stored_result = st.session_state.get("last_posts_result")
if stored_result and stored_result["request"] != request_state:
    st.session_state.pop("last_posts_result", None)

fetch_clicked = st.button("Fetch Last Posts", use_container_width=True)

if fetch_clicked:
    if not target:
        st.error(f"Please enter a {target_label.lower()}.")
        st.stop()

    loader = Loader()
    try:
        if platform == "Instagram":
            df = fetch_recent_instagram_posts(
                username=target,
                cookie_string=st.secrets.get("INSTAGRAM_COOKIE", ""),
                limit=limit,
                progress_callback=loader.update,
            )
        else:
            df = fetch_recent_youtube_posts(
                channel_input=target,
                api_key=st.secrets.get("YOUTUBE_API_KEY", ""),
                mode=mode,
                limit=limit,
                progress_callback=loader.update,
            )

        loader.done()
        if df.empty:
            st.warning("No posts found.")
            st.stop()

        st.session_state["last_posts_result"] = {
            "request": request_state,
            "df": df,
        }
    except Exception as error:
        loader.done()
        st.error(f"Error: {error}")


stored_result = st.session_state.get("last_posts_result")
if stored_result:
    render_results(
        stored_result["df"],
        stored_result["request"]["platform"],
        stored_result["request"]["target"],
    )
