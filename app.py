import streamlit as st

st.set_page_config(
    page_title="Social Media Tools",
    page_icon="📊",
    layout="wide"
)

st.title("📊 Social Media Tools")

st.markdown("""
Welcome to the Social Media Tools dashboard.

Available tools:

- Fetch Following
- Fetch Last Posts
- Extract Comments
""")

st.info("Select a tool from the sidebar.")