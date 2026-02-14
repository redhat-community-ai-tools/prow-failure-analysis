import json
import tempfile
from pathlib import Path

import pytest

from prow_failure_analysis.analysis.analyzer import (
    ArtifactAnalysis,
    FailureAnalyzer,
    RCAReport,
    StepAnalysis,
    _sanitize_json_string,
)
from prow_failure_analysis.gcs.models import JobResult, StepResult


class TestSanitizeJsonString:
    """Tests for _sanitize_json_string helper function."""

    def test_sanitize_plain_json(self):
        """Test that valid JSON passes through unchanged."""
        json_str = '{"key": "value", "number": 42}'
        result = _sanitize_json_string(json_str)
        assert json.loads(result) == {"key": "value", "number": 42}

    def test_sanitize_embedded_newlines(self):
        """Test that literal newlines in strings are escaped."""
        json_str = '{"message": "line1\nline2\nline3"}'
        result = _sanitize_json_string(json_str)
        parsed = json.loads(result)
        assert parsed["message"] == "line1\nline2\nline3"

    def test_sanitize_embedded_tabs(self):
        """Test that literal tabs in strings are escaped."""
        json_str = '{"message": "col1\tcol2\tcol3"}'
        result = _sanitize_json_string(json_str)
        parsed = json.loads(result)
        assert parsed["message"] == "col1\tcol2\tcol3"

    def test_sanitize_embedded_carriage_returns(self):
        """Test that literal carriage returns in strings are escaped."""
        json_str = '{"message": "line1\rline2"}'
        result = _sanitize_json_string(json_str)
        parsed = json.loads(result)
        assert parsed["message"] == "line1\rline2"

    def test_sanitize_mixed_control_chars(self):
        """Test that mixed control characters are all escaped."""
        json_str = '{"log": "error\n\tat com.example\r\n\tmore info"}'
        result = _sanitize_json_string(json_str)
        parsed = json.loads(result)
        assert "error" in parsed["log"]
        assert "com.example" in parsed["log"]

    def test_sanitize_array_with_control_chars(self):
        """Test sanitization works in JSON arrays."""
        json_str = '[{"source": "log.txt", "content": "error\ndetails"}]'
        result = _sanitize_json_string(json_str)
        parsed = json.loads(result)
        assert len(parsed) == 1
        assert parsed[0]["source"] == "log.txt"
        assert "error" in parsed[0]["content"]

    def test_sanitize_preserves_escaped_sequences(self):
        """Test that already-escaped sequences are preserved."""
        json_str = '{"message": "already\\nescaped\\ttabs"}'
        result = _sanitize_json_string(json_str)
        parsed = json.loads(result)
        assert "already\nescaped\ttabs" == parsed["message"]

    def test_sanitize_empty_strings(self):
        """Test sanitization handles empty strings."""
        json_str = '{"empty": ""}'
        result = _sanitize_json_string(json_str)
        assert json.loads(result) == {"empty": ""}

    def test_sanitize_nested_quotes(self):
        """Test sanitization handles nested escaped quotes."""
        json_str = '{"message": "said \\"hello\\""}'
        result = _sanitize_json_string(json_str)
        parsed = json.loads(result)
        assert parsed["message"] == 'said "hello"'


class TestArtifactBatchGenji:
    """Tests for Genji-based artifact batch analysis."""

    def test_single_artifact_produces_valid_json(self, mocker):
        """Test that a single artifact batch produces valid ArtifactAnalysis output."""
        from genji import MockBackend

        mocker.patch("prow_failure_analysis.analysis.analyzer.dspy")
        analyzer = FailureAnalyzer()
        analyzer._genji_backend = MockBackend(default_response="No significant findings.")

        result = analyzer._analyze_artifact_batch({"test.log": "some log content"}, batch_num=1)

        assert len(result) == 1
        assert result[0].artifact_path == "test.log"
        assert result[0].key_findings == "No significant findings."

    def test_multiple_artifacts_valid_json(self, mocker):
        """Test that multiple artifacts in a batch all produce valid output."""
        from genji import MockBackend

        mocker.patch("prow_failure_analysis.analysis.analyzer.dspy")
        analyzer = FailureAnalyzer()
        analyzer._genji_backend = MockBackend(default_response="Found error.")

        batch = {"a.yaml": "content a", "b.log": "content b", "c.json": "content c"}
        result = analyzer._analyze_artifact_batch(batch, batch_num=1)

        assert len(result) == 3
        paths = [r.artifact_path for r in result]
        assert "a.yaml" in paths
        assert "b.log" in paths
        assert "c.json" in paths
        for r in result:
            assert r.key_findings == "Found error."

    def test_empty_batch_returns_empty(self, mocker):
        """Test that an empty batch returns an empty list without calling the backend."""
        mocker.patch("prow_failure_analysis.analysis.analyzer.dspy")
        analyzer = FailureAnalyzer()

        result = analyzer._analyze_artifact_batch({}, batch_num=1)

        assert result == []

    def test_backend_error_returns_failure_entries(self, mocker):
        """Test that a backend error returns ArtifactAnalysis entries with error messages."""
        from genji import MockBackend

        mocker.patch("prow_failure_analysis.analysis.analyzer.dspy")
        analyzer = FailureAnalyzer()

        # Use a response_fn that raises to simulate backend failure
        def raise_error(prompt: str) -> str:
            raise RuntimeError("API down")

        analyzer._genji_backend = MockBackend(response_fn=raise_error)

        result = analyzer._analyze_artifact_batch({"test.log": "content"}, batch_num=1)

        assert len(result) == 1
        assert result[0].artifact_path == "test.log"
        assert "Analysis failed" in result[0].key_findings


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
        # Evidence now uses expandable details with source in summary
        assert "<details>" in md
        assert "<code>build.log</code>" in md
        assert "Error 1" in md
        assert "<code>compile.log</code>" in md
        assert "Error 2" in md

    def test_to_markdown_with_contributing_factors(self):
        """Test markdown generation includes LLM-ranked contributing factors."""
        report = RCAReport(
            job_name="test-job",
            build_id="12345",
            pr_number=None,
            summary="Test failed",
            detailed_analysis="Details here",
            category="test",
            step_analyses=[
                StepAnalysis(
                    step_name="test-step",
                    failure_category="test",
                    root_cause="Test failed",
                    evidence=[],
                ),
            ],
            artifact_analyses=[
                ArtifactAnalysis(artifact_path="pods/controller.log", key_findings="High memory usage detected."),
                ArtifactAnalysis(artifact_path="events.json", key_findings="Network timeout errors observed."),
                ArtifactAnalysis(artifact_path="unselected.yaml", key_findings="Disk errors found."),
                ArtifactAnalysis(artifact_path="empty.yaml", key_findings="No significant findings."),
            ],
            # LLM selected only these two as relevant
            contributing_artifact_paths=["pods/controller.log", "events.json"],
        )

        md = report.to_markdown()

        # Contributing Factors subsection appears within Evidence
        assert "## Evidence" in md
        assert "### Contributing Factors" in md
        # LLM-selected artifacts are included
        assert "<code>pods/controller.log</code>" in md
        assert "High memory usage detected." in md
        assert "<code>events.json</code>" in md
        assert "Network timeout errors observed." in md
        # Artifacts NOT in LLM's list are excluded even if they have findings
        assert "unselected.yaml" not in md
        assert "empty.yaml" not in md

    def test_to_markdown_contributing_factors_no_step_analyses(self):
        """Test contributing factors still renders when there are no step analyses."""
        report = RCAReport(
            job_name="test-job",
            build_id="12345",
            pr_number=None,
            summary="Test failed",
            detailed_analysis="Details here",
            category="test",
            step_analyses=[],
            artifact_analyses=[
                ArtifactAnalysis(artifact_path="pods/api.log", key_findings="Connection refused errors."),
            ],
            contributing_artifact_paths=["pods/api.log"],
        )

        md = report.to_markdown()

        assert "## Evidence" in md
        assert "### Contributing Factors" in md
        assert "<code>pods/api.log</code>" in md
        assert "Connection refused errors." in md

    def test_to_markdown_no_contributing_factors_when_empty_paths(self):
        """Test contributing factors section is omitted when LLM returns no paths."""
        report = RCAReport(
            job_name="test-job",
            build_id="12345",
            pr_number=None,
            summary="Test failed",
            detailed_analysis="Details here",
            category="test",
            step_analyses=[],
            artifact_analyses=[
                ArtifactAnalysis(artifact_path="a.yaml", key_findings="Some finding."),
            ],
            contributing_artifact_paths=[],
        )

        md = report.to_markdown()

        assert "### Contributing Factors" not in md

    def test_to_markdown_contributing_factors_filters_noise(self):
        """Test that LLM-selected paths with noise findings are still filtered out."""
        report = RCAReport(
            job_name="test-job",
            build_id="12345",
            pr_number=None,
            summary="Test failed",
            detailed_analysis="Details here",
            category="test",
            step_analyses=[],
            artifact_analyses=[
                ArtifactAnalysis(artifact_path="a.yaml", key_findings="No significant findings."),
                ArtifactAnalysis(artifact_path="b.log", key_findings="Analysis failed: timeout"),
            ],
            # LLM selected these but they're noise
            contributing_artifact_paths=["a.yaml", "b.log"],
        )

        md = report.to_markdown()

        assert "### Contributing Factors" not in md


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
