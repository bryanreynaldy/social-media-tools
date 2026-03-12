import pandas as pd
import streamlit as st
from io import BytesIO

def download_buttons(df):
    csv = df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")

    st.download_button(
        label="Download CSV",
        data=csv,
        file_name="comments.csv",
        mime="text/csv"
    )

    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)

    st.download_button(
        label="Download Excel",
        data=output.getvalue(),
        file_name="comments.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )