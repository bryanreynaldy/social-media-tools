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

/* Keep the working area calm and readable on wide Streamlit screens. */
.stMainBlockContainer,
section.main > div.block-container {
    max-width: 1180px;
    padding-top: 2.25rem;
    padding-bottom: 4rem;
}

/* Platform cards — target Streamlit's stable test IDs, not generated classes. */
[data-testid="stElementContainer"]:has([data-testid="stRadio"]),
[data-testid="stRadio"] {
    width: 100% !important;
}

[data-testid="stRadioGroup"] {
    display: grid !important;
    grid-template-columns: repeat(5, minmax(0, 1fr));
    gap: 14px !important;
    width: 100%;
}

[data-testid="stRadioOption"] {
    position: relative;
    display: flex !important;
    align-items: center;
    justify-content: center !important;
    min-width: 0 !important;
    width: 100% !important;
    box-sizing: border-box !important;
    min-height: 126px;
    padding: 18px 14px 16px !important;
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
    box-shadow: 0 0 0 3px rgba(96, 165, 250, .22), 0 14px 30px rgba(31, 73, 125, .30);
    transform: translateY(-4px);
}

[data-testid="stRadioOption"]:has(input:checked)::before {
    content: "";
    position: absolute;
    top: -2px;
    left: 18px;
    right: 18px;
    height: 5px;
    border-radius: 0 0 999px 999px;
    background: linear-gradient(90deg, #93c5fd, #3b82f6);
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

[data-testid="stRadioOption"]:has(input:checked) [data-testid="stMarkdownContainer"] img {
    border-color: #93c5fd !important;
    box-shadow: 0 0 0 3px rgba(147, 197, 253, .18);
}

[data-testid="stRadioOption"]:has(input:checked) p {
    text-shadow: 0 1px 2px rgba(0, 0, 0, .24);
}

/* Keep the native input accessible while removing Streamlit's radio artwork. */
[data-testid="stRadioOption"] > span,
[data-testid="stRadioOption"] > div > div:first-child > div:first-child {
    position: absolute !important;
    width: 1px !important;
    height: 1px !important;
    overflow: hidden !important;
    clip: rect(0 0 0 0) !important;
    opacity: 0 !important;
}

[data-testid="stRadioOption"] > div > div:first-child > div:first-child {
    display: none !important;
}

[data-testid="stRadioOption"] input[type="radio"] {
    position: absolute;
    width: 1px;
    height: 1px;
    opacity: 0;
}

[data-testid="stRadioOption"] > div:last-child {
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
    flex: 1 1 auto !important;
    min-height: 94px !important;
    height: auto !important;
}

[data-testid="stRadioOption"] > div:last-child > div:first-child {
    display: block !important;
    width: 100% !important;
}

[data-testid="stRadioOption"] [data-testid="stMarkdownContainer"] {
    display: block !important;
    flex: 1 1 auto !important;
    width: 100% !important;
    min-width: 0 !important;
}

[data-testid="stRadioOption"] p {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 10px;
    margin: 0;
    font-size: .98rem;
    font-weight: 700;
    white-space: nowrap;
    color: #f8fafc;
}

[data-testid="stRadioOption"] [data-testid="stMarkdownContainer"] img {
    display: block !important;
    width: 58px !important;
    min-width: 58px !important;
    max-width: 58px !important;
    height: 58px !important;
    min-height: 58px !important;
    max-height: 58px !important;
    padding: 8px !important;
    border: 1px solid #e4e4e4 !important;
    border-radius: 14px !important;
    background: #fff;
    object-fit: contain !important;
    box-sizing: border-box;
}

/* Compensate for the different source canvases without modifying the files. */
[data-testid="stRadioOption"] img[alt="YouTube"] {
    padding: 15px 7px !important;
}

[data-testid="stRadioOption"] img[alt="TikTok"] {
    padding: 5px !important;
}

/* Form and action styling */
div[data-testid="stTextInput"] input {
    min-height: 46px;
    border-radius: 11px;
}

div[data-testid="stButton"] button {
    min-height: 52px;
    border-radius: 12px;
    border: 1px solid #2f6fcb;
    background: #2f6fcb;
    color: #ffffff;
    font-weight: 700;
}

div[data-testid="stButton"] button:hover {
    border-color: #245cad;
    background: #245cad;
    color: #ffffff;
}

div[data-testid="stButton"] button:disabled {
    border-color: #5f7fa8;
    background: #5f7fa8;
    color: rgba(255, 255, 255, .78);
    opacity: .72;
}

div[data-testid="stMetric"] {
    padding: 18px 20px;
    border: 1px solid #e1e1e1;
    border-radius: 14px;
    background: #fafafa;
}

div[data-testid="stDownloadButton"] button {
    min-height: 44px;
    border-radius: 10px;
    font-weight: 650;
}

/* Only secondary result surfaces need a dark-specific treatment. */
@media (prefers-color-scheme: dark) {
    [data-testid="stRadioOption"] [data-testid="stMarkdownContainer"] img {
        border-color: #3b414d !important;
        background: #f7f7f8 !important;
    }

    div[data-testid="stMetric"] {
        border-color: #343944;
        background: #191d25;
    }
}

@media (max-width: 900px) {
    [data-testid="stRadioGroup"] {
        grid-template-columns: repeat(3, minmax(0, 1fr));
    }
}

@media (max-width: 640px) {
    .stMainBlockContainer,
    section.main > div.block-container {
        padding-top: 1.25rem;
    }

    [data-testid="stRadioGroup"] {
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 9px !important;
    }

    [data-testid="stRadioOption"] {
        min-height: 108px;
        padding: 14px 10px 12px !important;
    }

    [data-testid="stRadioOption"]:last-child:nth-child(odd) {
        grid-column: 1 / -1;
        justify-self: center;
        width: calc(50% - 5px) !important;
    }

    [data-testid="stRadioOption"] [data-testid="stMarkdownContainer"] img {
        width: 50px !important;
        min-width: 50px !important;
        max-width: 50px !important;
        height: 50px !important;
        min-height: 50px !important;
        max-height: 50px !important;
    }
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

url = st.text_input(
    "Post / Video Link",
    placeholder="Paste a public post or video link",
)

extract_clicked = st.button("Extract Comments", use_container_width=True)

if extract_clicked:
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

            with st.container(border=True):
                col1, col2 = st.columns([1, 2], vertical_alignment="center")

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

        reply_count = len(df[df["type"] == "reply"]) if "type" in df.columns else 0
        parent_count = len(df[df["type"] == "parent"]) if "type" in df.columns else len(df)

        st.success(f"Extraction complete — {len(df)} comments found.")

        col1, col2, col3 = st.columns(3)
        col1.metric("Total Comments", len(df))
        col2.metric("Parent Comments", parent_count)
        col3.metric("Replies", reply_count)

        st.subheader("Preview")
        st.dataframe(df.head(15), use_container_width=True)

        st.subheader("Download")
        download_buttons(df)

    except Exception as e:
        loader.done()
        st.error(f"Error: {e}")
