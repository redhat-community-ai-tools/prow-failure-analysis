from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..parsing.xunit_models import FailedTest


@dataclass
class FinishedMetadata:
    """Metadata from a finished.json file."""

    timestamp: datetime
    passed: bool
    result: str
    revision: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass
class StepResult:
    """Represents a single step execution result."""

    name: str
    passed: bool
    log_path: str | None
    log_size: int = 0
    timestamp: datetime | None = None
    finished_metadata: FinishedMetadata | None = None


@dataclass
class StageInfo:
    """Information about a pipeline stage."""

    name: str
    steps: list[str] = field(default_factory=list)


@dataclass
class JobResult:
    """Complete job execution result with all failed steps."""

    job_name: str
    build_id: str
    pr_number: str | None
    org_repo: str | None
    passed: bool
    failed_steps: list[StepResult]
    failed_tests: list["FailedTest"] = field(default_factory=list)
    step_graph: dict[str, Any] = field(default_factory=dict)
    stages: list[StageInfo] = field(default_factory=list)
    timestamp: datetime | None = None
    gcs_path: str = ""
    additional_artifacts: dict[str, str] = field(default_factory=dict)  # path -> content

    @property
    def is_pr_job(self) -> bool:
        """Check if this is a PR-triggered job."""
        return self.pr_number is not None

    @property
    def gcs_base_path(self) -> str:
        """Get the GCS base path for this job."""
        if self.is_pr_job:
            return f"pr-logs/pull/{self.org_repo}/{self.pr_number}/{self.job_name}/{self.build_id}"
        return f"logs/{self.job_name}/{self.build_id}"
