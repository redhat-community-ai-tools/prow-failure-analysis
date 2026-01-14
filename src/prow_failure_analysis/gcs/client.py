import json
import logging
import tempfile
from datetime import datetime
from typing import Any

from google.cloud import storage
from google.oauth2 import service_account

from ..parsing.xunit_models import FailedTest
from ..parsing.xunit_parser import XUnitParser
from .models import FinishedMetadata, JobResult, StageInfo, StepResult

logger = logging.getLogger(__name__)


class GCSClient:
    """Client for interacting with GCS bucket containing Prow logs."""

    def __init__(self, bucket_name: str, creds_path: str | None = None, config: Any = None) -> None:
        """Initialize GCS client.

        Args:
            bucket_name: Name of the GCS bucket
            creds_path: Optional path to service account credentials
            config: Optional Config instance for filtering settings
        """
        self.bucket_name = bucket_name
        self.config = config
        if creds_path:
            credentials = service_account.Credentials.from_service_account_file(creds_path)
            self.client = storage.Client(credentials=credentials)
        else:
            self.client = storage.Client.create_anonymous_client()
        self.bucket = self.client.bucket(bucket_name)
        self.xunit_parser = XUnitParser()

    def _parse_finished_json(self, content: str) -> FinishedMetadata | None:
        """Parse a finished.json file content.

        Args:
            content: JSON content as string

        Returns:
            FinishedMetadata object or None if parsing fails
        """
        try:
            data = json.loads(content)
            timestamp = datetime.fromtimestamp(data.get("timestamp", 0))
            return FinishedMetadata(
                timestamp=timestamp,
                passed=data.get("passed", False),
                result=data.get("result", "UNKNOWN"),
                revision=data.get("revision"),
                metadata=data.get("metadata"),
            )
        except (KeyError, ValueError) as e:
            logger.warning(f"Failed to parse finished.json: {e}")
            return None

    def _verify_blob_exists(self, blob_path: str) -> bool:
        """Check if a blob exists in the bucket.

        Args:
            blob_path: Path to blob in bucket

        Returns:
            True if blob exists, False otherwise
        """
        try:
            blob = self.bucket.blob(blob_path)
            exists: bool = blob.exists()
            return exists
        except Exception as e:
            logger.debug(f"Failed to check existence of {blob_path}: {e}")
            return False

    def _fetch_blob_text(self, blob_path: str) -> str | None:
        """Fetch a blob as text.

        Args:
            blob_path: Path to blob in bucket

        Returns:
            Blob content as string or None if not found
        """
        try:
            blob = self.bucket.blob(blob_path)
            content: str = blob.download_as_text()
            return content
        except Exception as e:
            # Distinguish between file not found and other errors
            error_str = str(e)
            if "404" in error_str or "Not Found" in error_str:
                logger.debug(f"File not found: {blob_path}")
            else:
                logger.warning(f"Failed to fetch {blob_path}: {e}")
            return None

    def _fetch_finished_json(self, base_path: str) -> FinishedMetadata | None:
        """Fetch and parse a finished.json file.

        Args:
            base_path: Base path to the directory containing finished.json

        Returns:
            FinishedMetadata or None
        """
        content = self._fetch_blob_text(f"{base_path}/finished.json")
        if content:
            return self._parse_finished_json(content)
        return None

    def _fetch_step_graph(self, base_path: str) -> dict[str, Any]:
        """Fetch the ci-operator-step-graph.json file.

        Args:
            base_path: Base path to artifacts directory

        Returns:
            Step graph as dictionary
        """
        content = self._fetch_blob_text(f"{base_path}/artifacts/ci-operator-step-graph.json")
        if content:
            try:
                data: dict[str, Any] = json.loads(content)
                return data
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse step graph: {e}")
        return {}

    def _list_stages(self, base_path: str) -> list[str]:
        """List all stage directories in artifacts.

        Args:
            base_path: Base path to job

        Returns:
            List of stage names
        """
        artifacts_prefix = f"{base_path}/artifacts/"
        blobs = self.client.list_blobs(self.bucket_name, prefix=artifacts_prefix, delimiter="/")

        stages = []
        for page in blobs.pages:
            for prefix in page.prefixes:
                stage_name = prefix.rstrip("/").split("/")[-1]
                # Skip non-stage artifacts
                if not stage_name.startswith("ci-operator") and stage_name not in ["build-resources", "release"]:
                    stages.append(stage_name)
        return stages

    def _list_steps_in_stage(self, base_path: str, stage_name: str) -> list[str]:
        """List all steps in a stage.

        Args:
            base_path: Base path to job
            stage_name: Name of the stage

        Returns:
            List of step names
        """
        stage_prefix = f"{base_path}/artifacts/{stage_name}/"
        blobs = self.client.list_blobs(self.bucket_name, prefix=stage_prefix, delimiter="/")

        steps = []
        for page in blobs.pages:
            for prefix in page.prefixes:
                step_name = prefix.rstrip("/").split("/")[-1]
                # Check if this is actually a step directory (has build-log.txt)
                if self._fetch_blob_text(f"{prefix}build-log.txt"):
                    steps.append(step_name)
        return steps

    def _fetch_step_result(self, base_path: str, stage_name: str, step_name: str) -> StepResult | None:
        """Fetch result for a single step.

        Args:
            base_path: Base path to job
            stage_name: Name of the stage
            step_name: Name of the step

        Returns:
            StepResult or None if step not found
        """
        step_path = f"{base_path}/artifacts/{stage_name}/{step_name}"

        # Fetch finished.json for this step
        finished_metadata = self._fetch_finished_json(step_path)
        if not finished_metadata:
            logger.debug(f"No finished.json for step {stage_name}/{step_name}")
            return None

        # Only fetch logs for failed steps
        log_path = None
        log_size = 0

        if not finished_metadata.passed:
            blob_path = f"{step_path}/build-log.txt"
            blob = self.bucket.blob(blob_path)

            try:
                # Stream logs to temp files to avoid memory issues
                blob.reload()  # Get metadata including size
                log_size = blob.size or 0

                if log_size > 0:
                    # Create temp file
                    tmp_file = tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False)
                    tmp_path = tmp_file.name
                    tmp_file.close()

                    # Stream download to file
                    blob.download_to_filename(tmp_path)
                    log_path = tmp_path
                    logger.debug(f"Step {stage_name}/{step_name}: streamed {log_size} bytes to temp file")
                else:
                    logger.warning(f"Failed step {stage_name}/{step_name} has empty build-log.txt")
            except Exception as e:
                logger.warning(f"Failed to fetch log for {stage_name}/{step_name}: {e}")

        return StepResult(
            name=f"{stage_name}/{step_name}",
            passed=finished_metadata.passed,
            log_path=log_path,
            log_size=log_size,
            timestamp=finished_metadata.timestamp,
            finished_metadata=finished_metadata,
        )

    def _is_xunit_file(self, blob_name: str) -> bool:
        """Check if blob name matches XUnit file patterns."""
        return blob_name.endswith(".xml") and (
            "junit" in blob_name or "report" in blob_name or "/results/" in blob_name or "/test-results/" in blob_name
        )

    def _should_include_xunit_file(self, blob_path: str) -> bool:
        """Check if XUnit file should be included based on config filters."""
        if not self.config:
            return True

        path_parts = blob_path.split("/")
        if len(path_parts) < 4 or "artifacts" not in path_parts:
            return True

        stage_idx = path_parts.index("artifacts")
        if stage_idx + 2 >= len(path_parts):
            return True

        stage = path_parts[stage_idx + 1]
        step = path_parts[stage_idx + 2]
        full_step_name = f"{stage}/{step}"

        if self.config.should_ignore_step(full_step_name):
            logger.debug(f"Ignoring XUnit file from filtered step: {full_step_name} ({blob_path})")
            return False

        return True

    def _list_xunit_files(self, base_path: str) -> list[str]:
        """List all XUnit XML files in artifacts directory.

        Args:
            base_path: Base path to job

        Returns:
            List of XUnit file paths that actually exist and are not filtered
        """
        artifacts_prefix = f"{base_path}/artifacts/"
        blobs = self.client.list_blobs(self.bucket_name, prefix=artifacts_prefix)

        xunit_files = []
        for blob in blobs:
            if not self._is_xunit_file(blob.name.lower()):
                continue

            if not self._should_include_xunit_file(blob.name):
                continue

            if self._verify_blob_exists(blob.name):
                xunit_files.append(blob.name)
            else:
                logger.warning(f"XUnit file pattern matched but doesn't exist: {blob.name}")

        return xunit_files

    def _fetch_xunit_results(self, base_path: str) -> list[FailedTest]:
        """Fetch and parse all XUnit test results.

        Args:
            base_path: Base path to job

        Returns:
            List of FailedTest objects
        """
        xunit_files = self._list_xunit_files(base_path)
        if not xunit_files:
            logger.debug("No XUnit files found")
            return []

        logger.info(f"Found {len(xunit_files)} XUnit files (validated)")

        all_failed_tests: list[FailedTest] = []
        successfully_fetched = 0

        for xunit_path in xunit_files:
            # Extract filename for logging
            source_file = xunit_path.split("/")[-1]

            try:
                content = self._fetch_blob_text(xunit_path)
                if content:
                    successfully_fetched += 1
                    failed_tests = self.xunit_parser.parse_xunit_file(content, source_file)
                    all_failed_tests.extend(failed_tests)
                    if failed_tests:
                        logger.info(f"Found {len(failed_tests)} failed tests in {source_file}")
                    else:
                        logger.debug(f"No failed tests in {source_file}")
                else:
                    logger.warning(f"Failed to fetch XUnit file: {source_file} (returned None)")
            except Exception as e:
                logger.warning(f"Error processing XUnit file {source_file}: {e}")
                continue

        if successfully_fetched < len(xunit_files):
            logger.warning(f"Only fetched {successfully_fetched}/{len(xunit_files)} XUnit files successfully")

        return all_failed_tests

    def _is_text_file(self, path: str) -> bool:
        """Check if file is a text/data file we want to analyze."""
        text_extensions = {
            ".json",
            ".xml",
            ".txt",
            ".log",
            ".yaml",
            ".yml",
            ".toml",
            ".ini",
            ".cfg",
            ".conf",
            ".properties",
            ".env",
            ".csv",
        }
        return any(path.lower().endswith(ext) for ext in text_extensions)

    def _fetch_artifacts_for_pattern(
        self, pattern: str, artifacts_prefix: str, max_depth: int = 3
    ) -> tuple[dict[str, str], int, int]:
        """Fetch artifacts matching a single pattern.

        Args:
            pattern: Glob pattern ending with /*
            artifacts_prefix: Base path prefix for artifacts
            max_depth: Maximum directory depth to search (default 2)

        Returns:
            Tuple of (artifacts_dict, total_checked, matched_count)
        """
        if not pattern.endswith("/*"):
            return {}, 0, 0

        dir_part = pattern[:-2]
        search_prefix = f"{artifacts_prefix}{dir_part}/"
        logger.debug(f"Fetching files from: {dir_part} (max_depth={max_depth})")

        blobs = self.client.list_blobs(self.bucket_name, prefix=search_prefix)

        artifacts = {}
        total = 0
        matched = 0
        skipped_depth = 0

        for blob in blobs:
            total += 1
            if blob.name.endswith("/"):
                continue

            # Check depth: count slashes after search_prefix
            relative_to_pattern = blob.name[len(search_prefix) :]
            depth = relative_to_pattern.count("/")
            if depth >= max_depth:
                skipped_depth += 1
                continue

            relative_path = blob.name[len(artifacts_prefix) :]
            if not self._is_text_file(relative_path):
                continue

            content = self._fetch_blob_text(blob.name)
            if content:
                artifacts[relative_path] = content
                matched += 1
                if matched % 10 == 0:
                    logger.info(f"Fetched {matched} artifacts...")
                logger.debug(f"Included artifact: {relative_path} ({len(content)} bytes)")

        if skipped_depth > 0:
            logger.info(f"Skipped {skipped_depth} files beyond depth {max_depth}")

        return artifacts, total, matched

    def _fetch_additional_artifacts(self, base_path: str) -> dict[str, str]:
        """Fetch additional artifact files based on configured patterns.

        Args:
            base_path: Base path to job

        Returns:
            Dictionary mapping artifact paths to their content
        """
        if not self.config or not self.config.included_artifacts_patterns:
            return {}

        patterns = [p.strip() for p in self.config.included_artifacts_patterns if p.strip()]
        if not patterns:
            return {}

        logger.info(f"Searching for artifacts matching: {', '.join(patterns)}")

        artifacts_prefix = f"{base_path}/artifacts/"
        all_artifacts = {}
        total_checked = 0
        total_matched = 0

        for pattern in patterns:
            artifacts, checked, matched = self._fetch_artifacts_for_pattern(pattern, artifacts_prefix)
            all_artifacts.update(artifacts)
            total_checked += checked
            total_matched += matched

        logger.info(f"Checked {total_checked} files, matched {total_matched}")

        if total_matched == 0 and total_checked > 0:
            logger.warning(f"No artifacts matched patterns: {', '.join(patterns)}")
            example_blobs = list(self.client.list_blobs(self.bucket_name, prefix=artifacts_prefix, max_results=3))
            example_paths = [blob.name[len(artifacts_prefix) :] for blob in example_blobs]
            logger.warning(f"Example paths checked: {example_paths}")

        return all_artifacts

    def _should_include_failed_step(self, step_result: StepResult) -> bool:
        """Check if failed step should be included based on config."""
        if self.config and self.config.should_ignore_step(step_result.name):
            logger.info(f"Ignoring failed step (filtered): {step_result.name}")
            return False
        logger.info(f"Found failed step: {step_result.name}")
        return True

    def _process_stage(self, base_path: str, stage_name: str) -> tuple[StageInfo | None, list[StepResult]]:
        """Process a single stage and collect step results.

        Returns:
            Tuple of (StageInfo or None, list of failed StepResults)
        """
        step_names = self._list_steps_in_stage(base_path, stage_name)
        logger.info(f"Stage {stage_name}: {len(step_names)} steps")

        stage_steps = []
        failed_steps = []

        for step_name in step_names:
            step_result = self._fetch_step_result(base_path, stage_name, step_name)
            if step_result:
                stage_steps.append(step_name)
                if not step_result.passed and self._should_include_failed_step(step_result):
                    failed_steps.append(step_result)

        stage_info = StageInfo(name=stage_name, steps=stage_steps) if stage_steps else None
        return stage_info, failed_steps

    def fetch_job_result(
        self,
        job_name: str,
        build_id: str,
        pr_number: str | None = None,
        org_repo: str | None = None,
    ) -> JobResult:
        """Fetch complete job result with all failed steps.

        Args:
            job_name: Name of the Prow job
            build_id: Build ID
            pr_number: PR number for PR-triggered jobs
            org_repo: Organization/repo for PR-triggered jobs

        Returns:
            JobResult object
        """
        base_path = (
            f"pr-logs/pull/{org_repo}/{pr_number}/{job_name}/{build_id}"
            if pr_number and org_repo
            else f"logs/{job_name}/{build_id}"
        )
        logger.info(f"Fetching job result from GCS: {base_path}")

        job_finished = self._fetch_finished_json(base_path)
        job_passed = job_finished.passed if job_finished else True
        job_timestamp = job_finished.timestamp if job_finished else None

        if not job_finished:
            logger.warning(f"No finished.json found at {base_path}")

        step_graph = self._fetch_step_graph(base_path)
        stage_names = self._list_stages(base_path)
        logger.info(f"Found {len(stage_names)} stages: {stage_names}")

        stages = []
        failed_steps = []
        for stage_name in stage_names:
            stage_info, stage_failed = self._process_stage(base_path, stage_name)
            if stage_info:
                stages.append(stage_info)
            failed_steps.extend(stage_failed)

        logger.info(f"Total failed steps: {len(failed_steps)}")

        failed_tests = self._fetch_xunit_results(base_path)
        logger.info(f"Total failed tests: {len(failed_tests)}")

        additional_artifacts = self._fetch_additional_artifacts(base_path)

        return JobResult(
            job_name=job_name,
            build_id=build_id,
            pr_number=pr_number,
            org_repo=org_repo,
            passed=job_passed,
            failed_steps=failed_steps,
            failed_tests=failed_tests,
            step_graph=step_graph,
            stages=stages,
            timestamp=job_timestamp,
            gcs_path=base_path,
            additional_artifacts=additional_artifacts,
        )
