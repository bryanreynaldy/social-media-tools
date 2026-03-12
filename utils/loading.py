import streamlit as st

class Loader:
    def __init__(self):
        self.text_placeholder = st.empty()
        self.progress_placeholder = st.empty()
        self.progress_bar = self.progress_placeholder.progress(0)

    def update(self, text, progress=None):
        self.text_placeholder.info(text)
        if progress is not None:
            progress = max(0, min(100, int(progress)))
            self.progress_bar.progress(progress)

    def done(self):
        self.text_placeholder.empty()
        self.progress_placeholder.empty()