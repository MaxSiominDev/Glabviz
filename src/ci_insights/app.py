import logging
from pathlib import Path

import streamlit as st

from ci_insights.config import configure_logging, data_dir
from ci_insights.runtime import Runtime
from ci_insights.storage import Storage, StorageError
from ci_insights.ui.data import render_data_page

logger = logging.getLogger("ci_insights.app")

st.set_page_config(page_title="CI Insights", page_icon=":bar_chart:", layout="wide")


@st.cache_resource
def _runtime(directory: str) -> Runtime:
    path = Path(directory)
    storage = Storage.open(path)
    configure_logging(path)
    storage.mark_interrupted_syncs()
    return Runtime(storage)


directory = data_dir()
try:
    runtime = _runtime(str(directory))
except StorageError as exc:
    st.title("CI Insights")
    st.error(f"Cannot open the local database: {exc}")
    st.stop()
except Exception as exc:
    logger.exception("failed to open the local database")
    st.title("CI Insights")
    st.error(f"Cannot open the local database: {type(exc).__name__}")
    st.stop()

st.title("CI Insights")
st.caption("Retrospective analysis of GitLab CI/CD")
st.sidebar.title("CI Insights")
st.sidebar.caption(f"Data directory: {directory}")
page = st.sidebar.radio("Page", ["Data"])
if page == "Data":
    render_data_page(runtime)
