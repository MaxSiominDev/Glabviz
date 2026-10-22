from __future__ import annotations

from datetime import UTC, datetime

import streamlit as st

from glabviz.domain import MetricPeriod, PipelineSnapshot
from glabviz.storage import Storage


@st.cache_data(show_spinner=False)
def load_period(
    _storage: Storage,
    revision: int,
    instance_base_url: str,
    project_id: int,
    period: MetricPeriod,
    source: str | None,
    ref: str | None,
    name: str | None,
) -> list[PipelineSnapshot]:
    start, end = period.utc_bounds()
    return _storage.load_snapshots(
        instance_base_url,
        project_id,
        start,
        end,
        source=source,
        ref=ref,
        name=name,
    )


@st.cache_data(show_spinner=False)
def load_all(
    _storage: Storage, revision: int, instance_base_url: str, project_id: int
) -> list[PipelineSnapshot]:
    return _storage.load_snapshots(
        instance_base_url,
        project_id,
        datetime(1970, 1, 1, tzinfo=UTC),
        datetime(2100, 1, 1, tzinfo=UTC),
    )
