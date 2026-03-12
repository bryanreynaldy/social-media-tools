import streamlit as st

from utils.download import download_buttons
from utils.loading import Loader

from extractors.youtube_extractor import extract_youtube_comments
from extractors.tiktok_extractor import extract_tiktok_comments
from extractors.instagram_extractor import extract_instagram_comments
from extractors.facebook_extractor import extract_facebook_comments

st.set_page_config(page_title="Extract Comments", page_icon="💬", layout="wide")

import base64
from pathlib import Path
import streamlit as st


def img_to_data_uri(path: str) -> str:
    file_bytes = Path(path).read_bytes()
    encoded = base64.b64encode(file_bytes).decode()
    return f"data:image/png;base64,{encoded}"


youtube_logo = img_to_data_uri("assets/youtube.png")
tiktok_logo = img_to_data_uri("assets/tiktok.png")
instagram_logo = img_to_data_uri("assets/instagram.png")
facebook_logo = img_to_data_uri("assets/facebook.png")

st.markdown("""
<style>

/* container radio */
div[role="radiogroup"]{
    gap: 1rem;
}

/* setiap tombol platform */
div[role="radiogroup"] > label{
    display:flex;
    align-items:center;
    justify-content:flex-start;
    gap:12px;

    padding:14px 18px;
    border-radius:14px;
    border:1px solid #ddd;
}

/* radio circle */
div[role="radiogroup"] input{
    margin:0;
}

/* logo platform */
div[role="radiogroup"] img{
    height:28px;
    width:28px;
    object-fit:contain;
}

</style>
""", unsafe_allow_html=True)

platform = st.radio(
    "Select Platform",
    [
        f"![yt]({youtube_logo}) YouTube",
        f"![tt]({tiktok_logo}) TikTok",
        f"![ig]({instagram_logo}) Instagram",
        f"![fb]({facebook_logo}) Facebook",
    ],
    horizontal=True
)

url = st.text_input("Post / Video Link")

if st.button("Extract Comments", use_container_width=True):
    if not url:
        st.error("Please enter a link.")
        st.stop()

    loader = Loader()

    try:
        loader.update("Reading link...", 5)

        if "YouTube" in platform:
            df, preview = extract_youtube_comments(
                url,
                progress_callback=loader.update,
                with_preview=True
            )

        elif "TikTok" in platform:
            df, preview = extract_tiktok_comments(
                url,
                progress_callback=loader.update,
                with_preview=True
            )

        elif "Instagram" in platform:
            df, preview = extract_instagram_comments(
                url,
                progress_callback=loader.update,
                with_preview=True
            )

        elif "Facebook" in platform:
            df, preview = extract_facebook_comments(
                url,
                progress_callback=loader.update,
                with_preview=True
            )

        else:
            loader.done()
            st.error("Unsupported platform.")
            st.stop()

        loader.update("Formatting results...", 95)

        if df.empty:
            loader.done()
            st.warning("No comments found.")
            st.stop()

        loader.update("Done", 100)
        loader.done()

        # =========================
        # POST PREVIEW
        # =========================
        if preview:
            st.subheader("Post Preview")

            col1, col2 = st.columns([1, 2])

            with col1:
                if preview.get("image"):
                    st.image(preview["image"], use_container_width=True)

            with col2:
                author = (preview.get("author") or "").strip()
                title = (preview.get("title") or "").strip()
                description = (preview.get("description") or "").strip()
                engagement = (preview.get("engagement") or "").strip()
                mode = preview.get("mode", "default")

                if author:
                    st.markdown(f"**Author**: {author}")

                if mode == "youtube":
                    if title:
                        st.markdown(f"**Title**: {title}")
                    if description:
                        st.markdown(f"**Description**: {description}")

                elif mode == "tiktok":
                    if title:
                        st.markdown(f"**Caption**: {title}")

                elif mode == "instagram":
                    if title:
                        st.markdown(f"**Caption**: {title}")

                elif mode == "facebook":
                    if description:
                        st.markdown(f"**Description**: {description}")

                else:
                    if title:
                        st.markdown(f"**Title / Caption**: {title}")
                    if description and description != title:
                        st.markdown(f"**Description**: {description}")

                if engagement:
                    st.markdown(f"**Engagement**: {engagement}")

        # =========================
        # SUMMARY
        # =========================
        st.success(f"Total comments extracted: {len(df)}")

        col1, col2 = st.columns(2)
        col1.metric("Total Rows", len(df))
        col2.metric("Replies", len(df[df["type"] == "reply"]))

        # =========================
        # PREVIEW TABLE
        # =========================
        st.subheader("Preview")
        st.dataframe(df.head(15), use_container_width=True)

        # =========================
        # DOWNLOAD
        # =========================
        st.subheader("Download")
        download_buttons(df)

    except Exception as e:
        loader.done()
        st.error(f"Error: {e}")