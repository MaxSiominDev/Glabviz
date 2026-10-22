# Metric contract

This document defines the metrics Glabviz uses across all screens, tables and
exports. Cards, charts and tables use the same normalized observations. Every page
follows the same calculation rules.

## Periods and samples

- A pipeline belongs to a period based on its `created_at` timestamp. Its stage and job
  observations belong to that same period, including late retries.
- A period covers two calendar dates in the selected IANA timezone, both inclusive. The
  database query uses `[start 00:00, 00:00 after the end date)`, converted through that
  timezone. This accounts for DST transitions without assuming each day lasts 24 hours.
- Keys: pipeline `(project_id, pipeline_id)`, stage `(project_id, pipeline_id,
  stage_name)`, job `(project_id, pipeline_id, stage_name, exact_full_job_name)`, attempt
  `job_id`. Parallel and matrix suffixes remain part of job names. Two pipelines with
  the same commit SHA count as two observations.
- Child and downstream pipelines remain separate projects or pipelines. Their jobs are
  not merged into the parent. The parent's bridge jobs are collected and count toward
  the parent.

## Classification

An attempt is a failure when `status == failed`. Only attempts with
`allow_failure=false` count toward classification and duration cohorts. Allowed failures
remain visible in tables and on the Gantt.

Terminal statuses: `success`, `failed`, `canceled`, `skipped`.

| Category | Rule |
|---|---|
| `red` | At least one counted failure in scope, including a canceled pipeline with an earlier counted failure |
| `green` | Terminal success, executed work, and no counted failure |
| `outside_fail` | Failures exist, but all are outside the selected stage scope |
| `canceled` | Canceled without a counted failure; excluded from the rate denominator |
| `no_execution` | Terminal, with no executed work and no failures |
| `unexplained_failed` | GitLab reports failed status, but no counted failure is visible |
| `not_settled` | A non-terminal status or an unsettled required attempt |
| `insufficient_data` | The snapshot is incomplete or inconsistent with the source |
| `other` | None of the rules above apply |

Stages and jobs are classified locally. A failed deploy does not turn an otherwise green
integration stage red. The pipeline stage scope affects only pipeline classification.

## Failure rate

```text
denominator = N_red + N_green + N_outside_fail
failure_rate = N_red / denominator
```

The denominator counts pipeline runs, stage runs or job runs, never attempts. A job with
two attempts still counts as one job run. If the denominator is zero, the value is
missing, not 0%. The UI shows the numerator, denominator, excluded categories and sample
size beside every value.

## Wall-clock

```text
pipeline = finished_at - started_at          (not GitLab duration)
stage    = max(finished_at of its executed jobs)
           - min(started_at of its executed jobs)
job      = last finish - first start across its attempts
```

- Only green objects enter duration statistics.
- A stage needs valid timestamps for every executed attempt. If an end timestamp is
  missing, the stage is excluded with a reason, rather than using a lower maximum.
- Pauses within the interval are included. Negative intervals are excluded, a valid zero
  is allowed, and values are rounded only for display.
- A job with several successful attempts counts as one observation, spanning the first
  start and the last finish.
- Pipeline wall-clock can include long bridge waits. Mean, P50, P95 and maximum are shown
  together to keep those long tails visible.

## Pending

- The source is `queued_duration` from job attempts. It is not recalculated as
  `started_at - created_at`, which can include dependency waits.
- The cohort consists of attempts belonging to green objects at the selected level:
  green pipelines, stages or job runs.
- Values come directly from attempts, not from per-pipeline averages. The UI shows both
  known and expected attempt counts. A missing value is not zero.
- The sum of parallel queued durations does not represent a pipeline wait and is never
  added to wall-clock.

## Comparison and dynamics

```text
absolute_delta = after - before
relative_percent = 100 * (after - before) / before
rate_delta_pp = 100 * (rate_after - rate_before)
```

If the baseline is zero, the relative change is missing, not infinity. If one side is
absent, all values for that side and all changes are missing. A job absent from a period
does not have a zero duration.

Each dynamics bucket is calculated from its own raw observations and counts. Empty days
or weeks appear as gaps, not 0%. Weeks start on Monday in the selected timezone. Monthly
values come from that month's raw observations, not from an average of daily values.

The stage/job comparison table uses a full outer join on exact keys. Rows present on
only one side are labelled, and the absent side stays missing.

## Data quality

- `snapshot_completeness` and `source_presence` are stored alongside the pipeline and
  attempts. The comparison screen reports incomplete pipelines and attempts that have
  disappeared from the API.
- A failed collection never publishes a partial snapshot as complete.
- "Complete" means all API-visible history for that pipeline was listed. It cannot prove
  that an administrator has never deleted an older attempt.
- Values are recalculated from the local database. A saved comparison stores settings,
  so its numbers can change after a sync.

## Limitations

The application does not analyse JUnit results, logs or flaky tests. It does not
reconstruct a historical `needs` graph or treat the longest job as a critical path.
Exports are browser downloads of individual Plotly figures, not HTML reports.