import json
import logging
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
        log_content = None
        log_size = 0
        if not finished_metadata.passed:
            log_content = self._fetch_blob_text(f"{step_path}/build-log.txt")
            if log_content:
                log_size = len(log_content)
            else:
                logger.warning(f"Failed step {stage_name}/{step_name} has no build-log.txt")

        return StepResult(
            name=f"{stage_name}/{step_name}",
            passed=finished_metadata.passed,
            log_content=log_content,
            log_size=log_size,
            timestamp=finished_metadata.timestamp,
            finished_metadata=finished_metadata,
        )

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
            blob_name = blob.name.lower()
            # Expanded patterns to catch more test result files:
            # - junit*.xml (standard JUnit files)
            # - *report*.xml (e2e-report.xml, test-report.xml)
            # - Files in results/ directories
            # - Files in test-results/ directories
            if blob_name.endswith(".xml") and (
                "junit" in blob_name
                or "report" in blob_name
                or "/results/" in blob_name
                or "/test-results/" in blob_name
            ):
                # Extract step name from path for filtering
                # Path format: {base}/artifacts/{stage}/{step}/artifacts/{file}.xml
                path_parts = blob.name.split("/")
                if len(path_parts) >= 4:
                    # Get stage/step ("appstudio-e2e-tests/redhat-appstudio-report")
                    stage_idx = path_parts.index("artifacts") if "artifacts" in path_parts else -1
                    if stage_idx >= 0 and stage_idx + 2 < len(path_parts):
                        stage = path_parts[stage_idx + 1]
                        step = path_parts[stage_idx + 2]
                        full_step_name = f"{stage}/{step}"

                        # Check if this step should be ignored
                        if self.config and self.config.should_ignore_step(full_step_name):
                            logger.debug(f"Ignoring XUnit file from filtered step: {full_step_name} ({blob.name})")
                            continue

                if self._verify_blob_exists(blob.name):
                    xunit_files.append(blob.name)
                else:
                    logger.warning(f"XUnit file pattern matched but file doesn't exist: {blob.name}")

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

    def _fetch_additional_artifacts(self, base_path: str) -> dict[str, str]:
        """Fetch additional artifact files based on configured patterns.

        Args:
            base_path: Base path to job

        Returns:
            Dictionary mapping artifact paths to their content
        """
        if not self.config or not self.config.included_artifacts_patterns:
            return {}

        # Filter out empty patterns
        patterns = [p.strip() for p in self.config.included_artifacts_patterns if p.strip()]
        if not patterns:
            return {}

        logger.info(f"Searching for artifacts matching: {', '.join(patterns)}")

        artifacts_prefix = f"{base_path}/artifacts/"
        additional_artifacts = {}
        matched_count = 0
        skipped_depth = 0
        total_checked = 0

        # Optimize: for patterns ending in /*, only get files in that exact directory
        # No recursion into subdirectories. It is faster and won't cause runaway recursion.
        for pattern in patterns:
            # Extract the directory part (everything before /*)
            if pattern.endswith("/*"):
                dir_part = pattern[:-2]  # Remove /*
                search_prefix = f"{artifacts_prefix}{dir_part}/"

                logger.debug(f"Fetching files from: {dir_part}")

                # Use delimiter to only get files in this directory (not subdirs)
                blobs = self.client.list_blobs(
                    self.bucket_name,
                    prefix=search_prefix,
                    delimiter="/",  # This makes it non-recursive
                )

                for blob in blobs:
                    total_checked += 1
                    # Skip directories (they end with /)
                    if blob.name.endswith("/"):
                        continue

                    relative_path = blob.name[len(artifacts_prefix) :]

                    # Skip binary/archive files
                    binary_extensions = {
                        ".tar",
                        ".gz",
                        ".zip",
                        ".tgz",
                        ".bz2",
                        ".xz",
                        ".7z",
                        ".bin",
                        ".exe",
                        ".png",
                        ".jpg",
                        ".jpeg",
                        ".gif",
                        ".pdf",
                    }
                    if any(relative_path.lower().endswith(ext) for ext in binary_extensions):
                        continue

                    # Fetch content
                    content = self._fetch_blob_text(blob.name)
                    if content:
                        additional_artifacts[relative_path] = content
                        matched_count += 1
                        logger.debug(f"Included artifact: {relative_path} ({len(content)} bytes)")

        logger.info(f"Checked {total_checked} files, matched {matched_count}, skipped {skipped_depth} (depth limit)")

        if matched_count == 0 and total_checked > 0:
            logger.warning(f"No artifacts matched patterns: {', '.join(patterns)}")
            example_blobs = list(self.client.list_blobs(self.bucket_name, prefix=artifacts_prefix, max_results=3))
            example_paths = [blob.name[len(artifacts_prefix) :] for blob in example_blobs]
            logger.warning(f"Example paths checked: {example_paths}")

        return additional_artifacts

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
        # Determine GCS path
        if pr_number and org_repo:
            base_path = f"pr-logs/pull/{org_repo}/{pr_number}/{job_name}/{build_id}"
            logger.info(f"Using PR-triggered job path with org_repo={org_repo}")
        else:
            base_path = f"logs/{job_name}/{build_id}"
            logger.info("Using periodic/non-PR job path")

        logger.info(f"Fetching job result from GCS path: {base_path}")

        # Fetch job-level finished.json
        job_finished = self._fetch_finished_json(base_path)
        if not job_finished:
            logger.warning(f"No finished.json found at {base_path}")
            job_passed = True
            job_timestamp = None
        else:
            job_passed = job_finished.passed
            job_timestamp = job_finished.timestamp

        # Fetch step graph
        step_graph = self._fetch_step_graph(base_path)

        # List all stages
        stage_names = self._list_stages(base_path)
        logger.info(f"Found {len(stage_names)} stages: {stage_names}")

        # Collect all step results
        stages = []
        failed_steps = []

        for stage_name in stage_names:
            step_names = self._list_steps_in_stage(base_path, stage_name)
            logger.info(f"Stage {stage_name}: {len(step_names)} steps")

            stage_steps = []
            for step_name in step_names:
                step_result = self._fetch_step_result(base_path, stage_name, step_name)
                if step_result:
                    stage_steps.append(step_name)
                    if not step_result.passed:
                        # Check if this step should be ignored
                        if self.config and self.config.should_ignore_step(step_result.name):
                            logger.info(f"Ignoring failed step (filtered): {step_result.name}")
                        else:
                            failed_steps.append(step_result)
                            logger.info(f"Found failed step: {step_result.name}")

            if stage_steps:
                stages.append(StageInfo(name=stage_name, steps=stage_steps))

        logger.info(f"Total failed steps: {len(failed_steps)}")

        # Fetch XUnit test results
        failed_tests = self._fetch_xunit_results(base_path)
        logger.info(f"Total failed tests: {len(failed_tests)}")

        # Fetch additional artifacts if configured
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
