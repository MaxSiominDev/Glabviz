from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import requests
from gitlab import Gitlab
from gitlab.exceptions import (
    GitlabAuthenticationError,
    GitlabError,
    GitlabHttpError,
    GitlabParsingError,
)

from glabviz.domain import (
    ProjectInfo,
    SnapshotCompleteness,
    parse_gitlab_timestamp,
)

DEFAULT_PER_PAGE = 100
MAX_PAGES = 10_000


class GitLabError(Exception):
    pass


class GitLabRequestError(GitLabError):
    def __init__(self, message: str, response_code: int | None = None) -> None:
        super().__init__(message)
        self.response_code = response_code


class GitLabAuthError(GitLabRequestError):
    pass


class GitLabNotFoundError(GitLabRequestError):
    pass


@dataclass(frozen=True)
class GitLabSettings:
    base_url: str
    token: str
    timeout_s: float = 60.0
    max_retries: int = 3
    ca_bundle: str | None = None
    per_page: int = DEFAULT_PER_PAGE
    snapshot_retries: int = 1


@dataclass(frozen=True)
class PipelineList:
    pipeline_ids: tuple[int, ...]
    warnings: tuple[str, ...] = ()
    lossy: bool = False


@dataclass(frozen=True)
class RawSnapshot:
    instance_base_url: str
    project_id: int
    detail: dict[str, Any]
    jobs: tuple[dict[str, Any], ...]
    current_job_ids: frozenset[int] | None
    bridges: tuple[dict[str, Any], ...]
    current_bridge_ids: frozenset[int] | None
    bridges_supported: bool
    completeness: SnapshotCompleteness
    snapshot_error: str | None = None


def canonical_instance_url(value: str) -> str:
    text = value.strip().rstrip("/")
    if text.endswith("/api/v4"):
        text = text[: -len("/api/v4")].rstrip("/")
    if not text:
        raise ValueError("GitLab base URL is required")
    if not (text.startswith("http://") or text.startswith("https://")):
        raise ValueError("GitLab base URL must start with http:// or https://")
    return text


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class GitLabClient:
    def __init__(self, settings: GitLabSettings) -> None:
        self._settings = settings
        self._local = threading.local()

    def _gl(self) -> Gitlab:
        client = getattr(self._local, "client", None)
        if client is None:
            client = Gitlab(
                self._settings.base_url,
                private_token=self._settings.token,
                timeout=self._settings.timeout_s,
                ssl_verify=self._settings.ca_bundle or True,
                per_page=self._settings.per_page,
                retry_transient_errors=True,
            )
            self._local.client = client
        return client

    def _get(
        self,
        path: str,
        query_data: dict[str, Any] | None = None,
    ) -> requests.Response:
        try:
            response = self._gl().http_get(
                path,
                query_data=query_data or {},
                raw=True,
                max_retries=self._settings.max_retries,
                retry_transient_errors=True,
            )
        except GitlabAuthenticationError as exc:
            raise GitLabAuthError(f"GitLab authentication failed: {exc}") from exc
        except GitlabHttpError as exc:
            code = exc.response_code
            message = f"GitLab request failed ({code}): {exc.error_message}"
            if code in (401, 403):
                raise GitLabAuthError(message, response_code=code) from exc
            if code == 404:
                raise GitLabNotFoundError(message, response_code=code) from exc
            raise GitLabRequestError(message, response_code=code) from exc
        except (GitlabError, GitlabParsingError, requests.RequestException) as exc:
            raise GitLabRequestError(f"GitLab request failed: {exc}") from exc
        if not isinstance(response, requests.Response):
            raise GitLabRequestError("GitLab returned an unexpected response object")
        return response

    def _get_json(self, path: str, query_data: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self._get(path, query_data)
        try:
            payload = response.json()
        except ValueError as exc:
            raise GitLabRequestError(f"GitLab returned invalid JSON for {path}") from exc
        if not isinstance(payload, dict):
            raise GitLabRequestError(f"GitLab returned an unexpected payload for {path}")
        return payload

    def _paginate(self, path: str, query: dict[str, Any]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        seen_ids: set[int] = set()
        page = 1
        while page <= MAX_PAGES:
            request_query = {
                **query,
                "per_page": self._settings.per_page,
                "page": page,
            }
            response = self._get(path, request_query)
            try:
                batch = response.json()
            except ValueError as exc:
                raise GitLabRequestError(f"GitLab returned invalid JSON for {path}") from exc
            if not isinstance(batch, list):
                raise GitLabRequestError(f"GitLab returned an unexpected payload for {path}")
            new_ids = 0
            for item in batch:
                item_id = item.get("id") if isinstance(item, dict) else None
                if isinstance(item_id, int) and item_id in seen_ids:
                    continue
                if isinstance(item_id, int):
                    seen_ids.add(item_id)
                    new_ids += 1
                items.append(item)
            next_page = (response.headers.get("X-Next-Page") or "").strip()
            if next_page:
                try:
                    next_number = int(next_page)
                except ValueError as exc:
                    raise GitLabRequestError(
                        f"GitLab returned an invalid X-Next-Page for {path}: {next_page}"
                    ) from exc
                if next_number <= 0:
                    break
                if next_number <= page:
                    raise GitLabRequestError(f"pagination did not advance for {path}")
                page = next_number
                continue
            total_pages = (response.headers.get("X-Total-Pages") or "").strip()
            if total_pages:
                try:
                    if page >= int(total_pages):
                        break
                    page += 1
                    continue
                except ValueError:
                    pass
            if len(batch) < self._settings.per_page:
                break
            if new_ids == 0:
                raise GitLabRequestError(f"pagination did not advance for {path}")
            page += 1
        if page > MAX_PAGES:
            raise GitLabRequestError(f"pagination limit exceeded for {path}")
        return items

    def check_project(self, project: str | int) -> ProjectInfo:
        encoded = quote(str(project), safe="")
        payload = self._get_json(f"/projects/{encoded}")
        try:
            return ProjectInfo(
                project_id=int(payload["id"]),
                path=str(payload["path_with_namespace"]),
                name=payload.get("name"),
                default_branch=payload.get("default_branch"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise GitLabRequestError("GitLab project payload is missing required fields") from exc

    def list_pipelines(
        self,
        project: str | int,
        created_after: datetime,
        created_before: datetime,
        *,
        source: str | None = None,
        ref: str | None = None,
        name: str | None = None,
    ) -> PipelineList:
        encoded = quote(str(project), safe="")
        path = f"/projects/{encoded}/pipelines"
        filtered_query: dict[str, Any] = {
            "created_after": _format_timestamp(created_after),
            "created_before": _format_timestamp(created_before),
            "order_by": "id",
            "sort": "asc",
        }
        if source:
            filtered_query["source"] = source
        if ref:
            filtered_query["ref"] = ref
        if name:
            filtered_query["name"] = name
        warnings: list[str] = []
        try:
            items = self._paginate(path, filtered_query)
        except GitLabRequestError as exc:
            if exc.response_code != 400:
                raise
            warnings.append(
                "the server rejected the pipeline filters; loaded a broad listing and "
                "applied the filters locally"
            )
            items = self._paginate(path, {"order_by": "id", "sort": "asc"})
        pipeline_ids: list[int] = []
        skipped_without_timestamp = 0
        skipped_without_id = 0
        for item in items:
            created_at = parse_gitlab_timestamp(item.get("created_at"))
            if created_at is None:
                skipped_without_timestamp += 1
                continue
            if not (created_after <= created_at < created_before):
                continue
            if source and item.get("source") != source:
                continue
            if ref and item.get("ref") != ref:
                continue
            if name and item.get("name") != name:
                continue
            try:
                pipeline_ids.append(int(item["id"]))
            except KeyError, TypeError, ValueError:
                skipped_without_id += 1
        if skipped_without_timestamp:
            warnings.append(
                f"skipped {skipped_without_timestamp} pipelines without a valid created_at"
            )
        if skipped_without_id:
            warnings.append(f"skipped {skipped_without_id} pipelines without a valid id")
        if not pipeline_ids:
            warnings.append("the server returned no pipelines for the requested filters")
        return PipelineList(
            pipeline_ids=tuple(pipeline_ids),
            warnings=tuple(warnings),
            lossy=skipped_without_timestamp > 0 or skipped_without_id > 0,
        )

    def fetch_snapshot(self, project: str | int, pipeline_id: int) -> RawSnapshot:
        last: RawSnapshot | None = None
        for _ in range(self._settings.snapshot_retries + 1):
            last = self._fetch_once(project, pipeline_id)
            if last.completeness in (
                SnapshotCompleteness.COMPLETE,
                SnapshotCompleteness.BRIDGES_UNSUPPORTED,
            ):
                return last
        assert last is not None
        return last

    def _fetch_once(self, project: str | int, pipeline_id: int) -> RawSnapshot:
        encoded = quote(str(project), safe="")
        base = f"/projects/{encoded}/pipelines/{pipeline_id}"
        detail_before = self._get_json(base)
        jobs_path = f"{base}/jobs"
        try:
            jobs = tuple(self._paginate(jobs_path, {"include_retried": "true"}))
            current_jobs = tuple(self._paginate(jobs_path, {"include_retried": "false"}))
            retried_supported = True
        except GitLabRequestError as exc:
            if exc.response_code != 400:
                raise
            jobs = tuple(self._paginate(jobs_path, {}))
            current_jobs = jobs
            retried_supported = False
        try:
            bridges = tuple(self._paginate(f"{base}/bridges", {"include_retried": "true"}))
            current_bridges = tuple(self._paginate(f"{base}/bridges", {"include_retried": "false"}))
            bridges_supported = True
        except GitLabRequestError as exc:
            if exc.response_code not in (400, 404):
                raise
            bridges = ()
            current_bridges = ()
            bridges_supported = False
        detail_after = self._get_json(base)

        job_ids = {int(job["id"]) for job in jobs}
        current_job_ids = {int(job["id"]) for job in current_jobs}
        missing_jobs = current_job_ids - job_ids
        missing_bridges: set[int] = set()
        current_bridge_ids: frozenset[int] | None = None
        if bridges_supported:
            bridge_ids = {int(bridge["id"]) for bridge in bridges}
            current_bridge_ids = frozenset(int(bridge["id"]) for bridge in current_bridges)
            missing_bridges = set(current_bridge_ids) - bridge_ids

        detail_changed = detail_before.get("updated_at") != detail_after.get(
            "updated_at"
        ) or detail_before.get("status") != detail_after.get("status")
        errors: list[str] = []
        if not retried_supported:
            errors.append("include_retried is not supported; retry history is unavailable")
        if detail_changed:
            errors.append("pipeline changed while its snapshot was being fetched")
        if missing_jobs:
            errors.append(f"current job ids missing from the full list: {sorted(missing_jobs)}")
        if missing_bridges:
            errors.append(
                f"current bridge ids missing from the full list: {sorted(missing_bridges)}"
            )
        if errors:
            completeness = SnapshotCompleteness.INCONSISTENT
            snapshot_error = "; ".join(errors)
        elif not bridges_supported:
            completeness = SnapshotCompleteness.BRIDGES_UNSUPPORTED
            snapshot_error = "bridges endpoint is not available"
        else:
            completeness = SnapshotCompleteness.COMPLETE
            snapshot_error = None
        try:
            project_id = int(detail_before["project_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise GitLabRequestError("GitLab pipeline payload is missing project_id") from exc
        return RawSnapshot(
            instance_base_url=self._settings.base_url,
            project_id=project_id,
            detail=detail_before,
            jobs=jobs,
            current_job_ids=frozenset(current_job_ids),
            bridges=bridges,
            current_bridge_ids=current_bridge_ids,
            bridges_supported=bridges_supported,
            completeness=completeness,
            snapshot_error=snapshot_error,
        )
