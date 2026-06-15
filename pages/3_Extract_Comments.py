import streamlit as st
import base64

from pathlib import Path

from utils.download import download_buttons
from utils.loading import Loader

from extractors.youtube_extractor import extract_youtube_comments
from extractors.tiktok_extractor import extract_tiktok_comments
from extractors.instagram_extractor import extract_instagram_comments
from extractors.facebook_extractor import extract_facebook_comments
from extractors.threads_extractor import extract_threads_comments


st.set_page_config(page_title="Extract Comments", page_icon="💬", layout="wide")


# ============================================================
# ASSET HELPERS
# ============================================================

def img_to_data_uri(path: str) -> str:
    file_path = Path(path)

    if not file_path.exists():
        return ""

    file_bytes = file_path.read_bytes()
    encoded = base64.b64encode(file_bytes).decode()

    return f"data:image/png;base64,{encoded}"


def platform_label(logo_uri: str, name: str, fallback_icon: str = "") -> str:
    if logo_uri:
        return f"![{name}]({logo_uri}) {name}"

    if fallback_icon:
        return f"{fallback_icon} {name}"

    return name


youtube_logo = img_to_data_uri("assets/youtube.png")
tiktok_logo = img_to_data_uri("assets/tiktok.png")
instagram_logo = img_to_data_uri("assets/instagram.png")
facebook_logo = img_to_data_uri("assets/facebook.png")
threads_logo = img_to_data_uri("assets/threads.png")


# ============================================================
# CSS
# ============================================================

st.markdown("""
<style>

div[role="radiogroup"]{
    gap: 1rem;
    flex-wrap: wrap;
}

div[role="radiogroup"] > label{
    display:flex;
    align-items:center;
    justify-content:flex-start;
    gap:12px;

    min-width: 185px;
    padding:14px 18px;
    border-radius:14px;
    border:1px solid #ddd;
}

div[role="radiogroup"] input{
    margin:0;
}

div[role="radiogroup"] img{
    height:28px;
    width:28px;
    object-fit:contain;
}

</style>
""", unsafe_allow_html=True)


youtube_option = platform_label(youtube_logo, "YouTube")
tiktok_option = platform_label(tiktok_logo, "TikTok")
instagram_option = platform_label(instagram_logo, "Instagram")
facebook_option = platform_label(facebook_logo, "Facebook")
threads_option = platform_label(threads_logo, "Threads", fallback_icon="🧵")


platform = st.radio(
    "Select Platform",
    [
        youtube_option,
        tiktok_option,
        instagram_option,
        facebook_option,
        threads_option,
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

        elif "Threads" in platform:
            df, preview = extract_threads_comments(
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

                elif mode == "threads":
                    if title:
                        st.markdown(f"**Post**: {title}")
                    if description:
                        st.markdown(f"**Description**: {description}")

                else:
                    if title:
                        st.markdown(f"**Title / Caption**: {title}")
                    if description and description != title:
                        st.markdown(f"**Description**: {description}")

                if engagement:
                    st.markdown(f"**Engagement**: {engagement}")

        st.success(f"Total comments extracted: {len(df)}")

        col1, col2 = st.columns(2)

        col1.metric("Total Rows", len(df))

        if "type" in df.columns:
            col2.metric("Replies", len(df[df["type"] == "reply"]))
        else:
            col2.metric("Replies", 0)

        st.subheader("Preview")
        st.dataframe(df.head(15), use_container_width=True)

        st.subheader("Download")
        download_buttons(df)

    except Exception as e:
        loader.done()
        st.error(f"Error: {e}")
