import streamlit as st

from ci_insights.config import data_dir

st.set_page_config(page_title="CI Insights", page_icon=":bar_chart:")

st.title("CI Insights")
st.caption("Retrospective analysis of GitLab CI/CD")
st.info("The sync and analysis screens will appear in the next iteration.")
st.text(f"Data directory: {data_dir()}")
