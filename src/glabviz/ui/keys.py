from __future__ import annotations

NAV = "nav_page"
NAV_TARGET = "_navigate_to"
ACTIVE_ANALYSIS_ID = "active_analysis_id"
SELECTED_ANALYSIS = "saved_selected_id"
SELECTED_PENDING = "_saved_selected_pending"
SAVE_NAME = "save_name"
SAVED_RENAME_NAME = "saved_rename_name"
SAVED_DELETE_CONFIRM = "saved_delete_confirm"
SAVED_SELECTION_MARKER = "_saved_selection_marker"
COMPARE_PREVIOUS_PROJECT = "_compare_previous_project"
GANTT_PREVIOUS_PROJECT = "_gantt_previous_project"
ALL_STAGES = "_all_stages"
GANTT_PENDING_HIDDEN = "_gantt_pending_hidden"
GANTT_STATE = "_gantt_state"

C_PROJECT = "compare_project"
C_SOURCE = "compare_source"
C_REF = "compare_ref"
C_NAME = "compare_name"
C_TIMEZONE = "compare_timezone"
C_A_START = "compare_a_start"
C_A_END = "compare_a_end"
C_B_START = "compare_b_start"
C_B_END = "compare_b_end"
C_LEVEL = "compare_level"
C_STAGE = "compare_stage"
C_JOB_STAGE = "compare_job_stage"
C_JOB = "compare_job"
C_SCOPE_ENABLED = "compare_scope_enabled"
C_SCOPE_STAGES = "compare_scope_stages"
C_WALL_METRIC = "compare_wall_metric"
C_PENDING_METRIC = "compare_pending_metric"
C_DYNAMICS_METRIC = "compare_dynamics_metric"
C_GRANULARITY = "compare_granularity"
C_INTEGRATION_PREFIX = "compare_integration_prefix"
C_TABLE = "compare_table"
C_TABLE_SEARCH = "compare_table_search"
C_TABLE_STAGE = "compare_table_stage"

E_FORMAT = "export_format"
E_WIDTH = "export_width"
E_HEIGHT = "export_height"
E_FONT_SIZE = "export_font_size"
E_SCALE = "export_scale"

G_SEARCH = "gantt_search"
G_JOB_SEARCH = "gantt_job_search"
DYNAMICS_TEXT_METRIC = "_dynamics_text_metric"
G_PIPELINE_A = "gantt_pipeline_a"
G_PIPELINE_B = "gantt_pipeline_b"
G_VISIBLE_STAGES = "gantt_visible_stages"
G_VISIBLE_JOBS = "gantt_visible_jobs"
G_SORT_BY = "gantt_sort_by"
G_SORT_SCOPE = "gantt_sort_scope"
G_DESCENDING = "gantt_descending"
G_RANGE_START = "gantt_range_start"
G_RANGE_END = "gantt_range_end"

CHART_IDS = ("failure", "wall", "distribution", "pending", "dynamics", "gantt")


def chart_key(chart_id: str, field: str) -> str:
    return f"chart_{chart_id}_{field}"


def job_option_key(stage: str, name: str) -> str:
    return f"{stage}\x00{name}"


def split_job_option(value: str) -> tuple[str, str]:
    stage, _, name = value.partition("\x00")
    return stage, name
