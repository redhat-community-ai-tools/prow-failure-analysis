import tempfile
from pathlib import Path

import pytest

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
            category="test",
            step_analyses=[],
        )

        md = report.to_markdown()

        assert "# Pipeline Failure Analysis" in md
        assert "**Job:** `test-job`" in md
        assert "**Build:** `12345`" in md
        assert "**Category:** Test" in md
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
            category="build",
            step_analyses=[],
        )

        md = report.to_markdown()

        assert "**PR:** #999" in md
        assert "**Category:** Build" in md

    def test_to_markdown_category_display(self):
        """Test markdown generation displays category."""
        report = RCAReport(
            job_name="test-job",
            build_id="12345",
            pr_number=None,
            summary="Test failed",
            detailed_analysis="Details here",
            category="infrastructure",
            step_analyses=[],
        )

        md = report.to_markdown()

        assert "**Category:** Infrastructure" in md

    def test_to_markdown_with_step_evidence(self):
        """Test markdown generation includes step evidence."""
        step_analysis = StepAnalysis(
            step_name="test-stage/test-step",
            failure_category="build",
            root_cause="Build failed",
            evidence=[
                {"source": "build.log", "content": "Error 1"},
                {"source": "compile.log", "content": "Error 2"},
            ],
        )

        report = RCAReport(
            job_name="test-job",
            build_id="12345",
            pr_number=None,
            summary="Test failed",
            detailed_analysis="Details here",
            category="build",
            step_analyses=[step_analysis],
        )

        md = report.to_markdown()

        assert "## Evidence" in md
        assert "**test-stage/test-step** — *build*" in md
        assert "**build.log:**" in md
        assert "`Error 1`" in md
        assert "**compile.log:**" in md
        assert "`Error 2`" in md


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
        """Test building artifacts context with artifact analyses."""
        mocker.patch("prow_failure_analysis.analysis.analyzer.dspy")
        analyzer = FailureAnalyzer()

        from prow_failure_analysis.analysis.analyzer import ArtifactAnalysis

        artifact_analyses = [
            ArtifactAnalysis(artifact_path="cluster-state.yaml", key_findings="Cluster state looks normal"),
            ArtifactAnalysis(artifact_path="long-file.txt", key_findings="Found critical error in logs"),
        ]

        artifacts_dict = analyzer._build_artifacts_context(artifact_analyses)

        assert "note" in artifacts_dict
        assert "analyses" in artifacts_dict
        assert len(artifacts_dict["analyses"]) == 2
        assert artifacts_dict["analyses"][0]["artifact_path"] == "cluster-state.yaml"
        assert artifacts_dict["analyses"][0]["key_findings"] == "Cluster state looks normal"
        assert artifacts_dict["analyses"][1]["artifact_path"] == "long-file.txt"
        assert artifacts_dict["analyses"][1]["key_findings"] == "Found critical error in logs"

    def test_forward_raises_on_no_failures(self, mocker):
        """Test forward raises ValueError when there are no failures."""
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

        with pytest.raises(ValueError, match="No failures to analyze"):
            analyzer.forward(job_result)
