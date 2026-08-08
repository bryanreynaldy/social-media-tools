import base64
from io import BytesIO
from pathlib import Path

import pandas as pd
import streamlit as st

from extractors.instagram_following_extractor import (
    extract_instagram_following,
    normalize_username,
)
from utils.loading import Loader


st.set_page_config(page_title="Fetch Following", page_icon="📷", layout="wide")


def img_to_data_uri(path: str) -> str:
    file_path = Path(path)
    if not file_path.exists():
        return ""
    encoded = base64.b64encode(file_path.read_bytes()).decode()
    return f"data:image/png;base64,{encoded}"


instagram_logo = img_to_data_uri("assets/instagram.png")


st.markdown("""
<style>
.stMainBlockContainer,
section.main > div.block-container {
    max-width: 1120px;
    padding-top: 2.35rem;
    padding-bottom: 4rem;
}

.following-brand {
    display: flex;
    align-items: center;
    gap: 14px;
    min-height: 82px;
    margin-bottom: 1.15rem;
    padding: 15px 18px;
    border: 1px solid #42526a;
    border-radius: 16px;
    background: linear-gradient(145deg, #30415a 0%, #273449 75%);
    box-shadow: 0 12px 28px rgba(31, 73, 125, .18);
}

.following-brand img {
    width: 52px;
    height: 52px;
    padding: 7px;
    border: 1px solid #d8dee8;
    border-radius: 14px;
    background: #f8fafc;
    object-fit: contain;
    box-sizing: border-box;
}

.following-brand__copy {
    display: flex;
    flex-direction: column;
    gap: 1px;
}

.following-brand__platform {
    color: #b9d7fb;
    font-size: .78rem;
    font-weight: 700;
    letter-spacing: .08em;
    text-transform: uppercase;
}

.following-brand__title {
    color: #f8fafc;
    font-size: 1.28rem;
    font-weight: 750;
    line-height: 1.2;
}

div[data-testid="stTextInput"] input {
    min-height: 50px;
    border-radius: 12px;
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
    min-height: 116px;
    padding: 18px 20px;
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

@media (max-width: 640px) {
    .stMainBlockContainer,
    section.main > div.block-container {
        padding-top: 1.25rem;
    }

    .following-brand {
        min-height: 72px;
        padding: 12px 14px;
    }

    .following-brand img {
        width: 46px;
        height: 46px;
    }

    .following-brand__title {
        font-size: 1.12rem;
    }
}
</style>
""", unsafe_allow_html=True)


def render_results(df, username: str):
    total = len(df)
    named = int(df["full_name"].fillna("").str.strip().ne("").sum()) if total else 0
    unnamed = total - named
    completeness = round((named / total) * 100) if total else 0

    st.success(f"Complete — {total:,} accounts found for @{username}.")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Following", f"{total:,}")
    col2.metric("Named Profiles", f"{named:,}")
    col3.metric("No Full Name", f"{unnamed:,}")
    col4.metric("Profile Details", f"{completeness}%")

    st.subheader("Preview")
    preview_df = df[["index", "username", "full_name", "profile_url"]].head(30)
    st.dataframe(
        preview_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "index": st.column_config.NumberColumn("#", width="small"),
            "username": st.column_config.TextColumn("Username"),
            "full_name": st.column_config.TextColumn("Full Name"),
            "profile_url": st.column_config.LinkColumn("Profile", display_text="Open"),
        },
    )

    st.subheader("Download")
    following_download_buttons(df, username)


def following_download_buttons(df, username: str):
    file_name = f"{username}_following"
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


logo_html = f'<img src="{instagram_logo}" alt="Instagram">' if instagram_logo else ""
st.markdown(
    f"""
    <div class="following-brand">
        {logo_html}
        <div class="following-brand__copy">
            <span class="following-brand__platform">Instagram</span>
            <span class="following-brand__title">Fetch Following</span>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

username_input = st.text_input(
    "Target Username",
    placeholder="username",
).strip()
username = normalize_username(username_input)

stored_result = st.session_state.get("following_result")
if stored_result and stored_result["username"] != username:
    st.session_state.pop("following_result", None)

fetch_clicked = st.button("Fetch Following", use_container_width=True)

if fetch_clicked:
    if not username:
        st.error("Please enter an Instagram username.")
        st.stop()

    loader = Loader()
    try:
        cookie_string = st.secrets.get("INSTAGRAM_COOKIE", "")
        df = extract_instagram_following(
            target_username=username,
            cookie_string=cookie_string,
            progress_callback=loader.update,
        )
        loader.done()

        if df.empty:
            st.warning("No following accounts found.")
            st.stop()

        st.session_state["following_result"] = {
            "username": username,
            "df": df,
        }
    except Exception as error:
        loader.done()
        st.error(f"Error: {error}")


stored_result = st.session_state.get("following_result")
if stored_result:
    render_results(stored_result["df"], stored_result["username"])
