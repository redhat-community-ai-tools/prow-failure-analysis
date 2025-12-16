from prow_failure_analysis.gcs.models import JobResult


class TestJobResult:
    """Tests for JobResult dataclass properties with custom logic."""

    def test_is_pr_job_true(self) -> None:
        """Test is_pr_job returns True when pr_number is set."""
        job_result = JobResult(
            job_name="test-job",
            build_id="12345",
            pr_number="42",
            org_repo="org/repo",
            passed=True,
            failed_steps=[],
        )
        assert job_result.is_pr_job is True

    def test_is_pr_job_false(self) -> None:
        """Test is_pr_job returns False when pr_number is None."""
        job_result = JobResult(
            job_name="test-job",
            build_id="12345",
            pr_number=None,
            org_repo=None,
            passed=True,
            failed_steps=[],
        )
        assert job_result.is_pr_job is False

    def test_gcs_base_path_pr_job(self) -> None:
        """Test gcs_base_path for PR-triggered job."""
        job_result = JobResult(
            job_name="pull-ci-test",
            build_id="98765",
            pr_number="123",
            org_repo="kubernetes/kubernetes",
            passed=False,
            failed_steps=[],
        )
        assert job_result.gcs_base_path == "pr-logs/pull/kubernetes/kubernetes/123/pull-ci-test/98765"

    def test_gcs_base_path_periodic_job(self) -> None:
        """Test gcs_base_path for periodic/non-PR job."""
        job_result = JobResult(
            job_name="periodic-job",
            build_id="54321",
            pr_number=None,
            org_repo=None,
            passed=True,
            failed_steps=[],
        )
        assert job_result.gcs_base_path == "logs/periodic-job/54321"

    def test_gcs_base_path_consistency_with_gcs_path(self) -> None:
        """Test that gcs_base_path property matches expected format."""
        # PR job
        pr_job = JobResult(
            job_name="test-pr",
            build_id="111",
            pr_number="999",
            org_repo="org/repo",
            passed=True,
            failed_steps=[],
            gcs_path="pr-logs/pull/org/repo/999/test-pr/111",
        )
        assert pr_job.gcs_base_path == pr_job.gcs_path

        # Periodic job
        periodic_job = JobResult(
            job_name="test-periodic",
            build_id="222",
            pr_number=None,
            org_repo=None,
            passed=True,
            failed_steps=[],
            gcs_path="logs/test-periodic/222",
        )
        assert periodic_job.gcs_base_path == periodic_job.gcs_path
