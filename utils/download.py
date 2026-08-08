import pandas as pd
import streamlit as st
from io import BytesIO

def download_buttons(df, file_name="comments"):
    csv = df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")

    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)

    csv_col, excel_col = st.columns(2)

    with csv_col:
        st.download_button(
            label="Download CSV",
            data=csv,
            file_name=f"{file_name}.csv",
            mime="text/csv",
            use_container_width=True,
            on_click="ignore",
        )

    with excel_col:
        st.download_button(
            label="Download Excel",
            data=output.getvalue(),
            file_name=f"{file_name}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            on_click="ignore",
        )
