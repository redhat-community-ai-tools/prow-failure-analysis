import tempfile
from pathlib import Path

from prow_failure_analysis.analysis.analyzer import FailureAnalyzer, RCAReport, StepAnalysis
from prow_failure_analysis.gcs.models import JobResult, StepResult


class TestRCAReport:
    """Tests for RCAReport markdown generation."""

    def test_to_markdown_basic(self):
        """Test markdown generation with basic info."""
        report = RCAReport(
            job_name="test-job",
            build_id="12345",
            pr_number=None,
            summary="Test failed",
            detailed_analysis="Details here",
            is_infrastructure=False,
            step_analyses=[],
        )

        md = report.to_markdown()

        assert "# Pipeline Failure Analysis" in md
        assert "**Job:** `test-job`" in md
        assert "**Build:** `12345`" in md
        assert "## Root Cause" in md
        assert "Test failed" in md
        assert "## Technical Details" in md
        assert "Details here" in md

    def test_to_markdown_with_pr(self):
        """Test markdown generation includes PR number."""
        report = RCAReport(
            job_name="test-job",
            build_id="12345",
            pr_number="999",
            summary="Test failed",
            detailed_analysis="Details here",
            is_infrastructure=False,
            step_analyses=[],
        )

        md = report.to_markdown()

        assert "**PR:** #999" in md

    def test_to_markdown_infrastructure_flag(self):
        """Test markdown generation includes infrastructure warning."""
        report = RCAReport(
            job_name="test-job",
            build_id="12345",
            pr_number=None,
            summary="Test failed",
            detailed_analysis="Details here",
            is_infrastructure=True,
            step_analyses=[],
        )

        md = report.to_markdown()

        assert "**Infrastructure Issue** ⚠️" in md

    def test_to_markdown_with_step_evidence(self):
        """Test markdown generation includes step evidence."""
        step_analysis = StepAnalysis(
            step_name="test-stage/test-step",
            failure_category="build",
            root_cause="Build failed",
            evidence=["Error 1", "Error 2"],
        )

        report = RCAReport(
            job_name="test-job",
            build_id="12345",
            pr_number=None,
            summary="Test failed",
            detailed_analysis="Details here",
            is_infrastructure=False,
            step_analyses=[step_analysis],
        )

        md = report.to_markdown()

        assert "## Evidence" in md
        assert "**test-stage/test-step** — *build*" in md
        assert "- Error 1" in md
        assert "- Error 2" in md


class TestFailureAnalyzer:
    """Tests for FailureAnalyzer custom logic."""

    def test_read_log_content_success(self, mocker):
        """Test reading log content from temp file."""
        mocker.patch("prow_failure_analysis.analysis.analyzer.dspy")
        analyzer = FailureAnalyzer()

        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log") as f:
            f.write("test log content")
            temp_path = f.name

        try:
            step = StepResult(name="test-step", passed=False, log_path=temp_path, log_size=0)

            content = analyzer._read_log_content(step)

            assert content == "test log content"
        finally:
            Path(temp_path).unlink()

    def test_read_log_content_no_path(self, mocker):
        """Test reading log when no path is set."""
        mocker.patch("prow_failure_analysis.analysis.analyzer.dspy")
        analyzer = FailureAnalyzer()

        step = StepResult(name="test-step", passed=False, log_path=None, log_size=0)

        content = analyzer._read_log_content(step)

        assert content == "(No log content available)"

    def test_read_log_content_file_not_found(self, mocker):
        """Test reading log handles missing file."""
        mocker.patch("prow_failure_analysis.analysis.analyzer.dspy")
        analyzer = FailureAnalyzer()

        step = StepResult(name="test-step", passed=False, log_path="/nonexistent.log", log_size=0)

        content = analyzer._read_log_content(step)

        assert content == "(No log content available)"

    def test_get_step_context_no_graph(self, mocker):
        """Test step context when no graph available."""
        mocker.patch("prow_failure_analysis.analysis.analyzer.dspy")
        analyzer = FailureAnalyzer()

        step = StepResult(name="test-stage/test-step", passed=False, log_path=None, log_size=0)

        context = analyzer._get_step_context(step, {})

        assert context == "Step test-stage/test-step - no graph information available"

    def test_get_step_context_with_dependencies(self, mocker):
        """Test step context extracts dependencies from graph."""
        mocker.patch("prow_failure_analysis.analysis.analyzer.dspy")
        analyzer = FailureAnalyzer()

        step = StepResult(name="test-stage/test-step", passed=False, log_path=None, log_size=0)

        step_graph = {"nodes": [{"name": "test-step", "dependencies": ["dep1", "dep2"]}]}

        context = analyzer._get_step_context(step, step_graph)

        assert "dependencies: ['dep1', 'dep2']" in context

    def test_get_step_context_no_matching_node(self, mocker):
        """Test step context when step not found in graph."""
        mocker.patch("prow_failure_analysis.analysis.analyzer.dspy")
        analyzer = FailureAnalyzer()

        step = StepResult(name="test-stage/unknown-step", passed=False, log_path=None, log_size=0)

        step_graph = {"nodes": [{"name": "other-step", "dependencies": []}]}

        context = analyzer._get_step_context(step, step_graph)

        assert context == "Step test-stage/unknown-step - part of pipeline execution"

    def test_build_artifacts_context_empty(self, mocker):
        """Test building artifacts context with no artifacts."""
        mocker.patch("prow_failure_analysis.analysis.analyzer.dspy")
        analyzer = FailureAnalyzer()

        artifacts_dict = analyzer._build_artifacts_context(None)

        assert artifacts_dict == {}

    def test_build_artifacts_context_with_files(self, mocker):
        """Test building artifacts context with files."""
        mocker.patch("prow_failure_analysis.analysis.analyzer.dspy")
        analyzer = FailureAnalyzer()

        artifacts = {
            "cluster-state.yaml": "short content",
            "long-file.txt": "a" * 2000,
        }

        artifacts_dict = analyzer._build_artifacts_context(artifacts)

        assert "note" in artifacts_dict
        assert "files" in artifacts_dict
        assert "cluster-state.yaml" in artifacts_dict["files"]
        assert artifacts_dict["files"]["cluster-state.yaml"]["content_preview"] == "short content"
        assert artifacts_dict["files"]["long-file.txt"]["size"] == 2000
        assert len(artifacts_dict["files"]["long-file.txt"]["content_preview"]) == 1000

    def test_create_empty_report(self, mocker):
        """Test creating report for jobs with no failures."""
        mocker.patch("prow_failure_analysis.analysis.analyzer.dspy")
        analyzer = FailureAnalyzer()

        job_result = JobResult(
            job_name="test-job",
            build_id="12345",
            pr_number="999",
            org_repo="org/repo",
            passed=True,
            failed_steps=[],
        )

        report = analyzer._create_empty_report(job_result)

        assert report.job_name == "test-job"
        assert report.build_id == "12345"
        assert report.pr_number == "999"
        assert "No failures detected" in report.summary
        assert report.is_infrastructure is False
