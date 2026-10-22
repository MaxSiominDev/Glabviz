import logging
from pathlib import Path

import streamlit as st

from glabviz.config import configure_logging, data_dir
from glabviz.runtime import Runtime
from glabviz.storage import Storage, StorageError
from glabviz.ui import keys
from glabviz.ui.compare import render_compare_page
from glabviz.ui.data import render_data_page
from glabviz.ui.gantt import render_gantt_page
from glabviz.ui.saved import render_saved_page

logger = logging.getLogger("glabviz.app")

st.set_page_config(page_title="Glabviz", page_icon=":bar_chart:", layout="wide")


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
    st.title("Glabviz")
    st.error(f"Cannot open the local database: {exc}")
    st.stop()
except Exception as exc:
    logger.exception("failed to open the local database")
    st.title("Glabviz")
    st.error(f"Cannot open the local database: {type(exc).__name__}")
    st.stop()

PAGES = ["Data", "Compare", "Gantt", "Saved"]
target = st.session_state.pop(keys.NAV_TARGET, None)
if target in PAGES:
    st.session_state[keys.NAV] = target

st.title("Glabviz")
st.caption("Retrospective analysis of GitLab CI/CD")
st.sidebar.title("Glabviz")
st.sidebar.caption(f"Data directory: {directory}")
page = st.sidebar.radio("Page", PAGES, key=keys.NAV)
if page == "Data":
    render_data_page(runtime)
elif page == "Compare":
    render_compare_page(runtime)
elif page == "Gantt":
    render_gantt_page(runtime)
else:
    render_saved_page(runtime)
