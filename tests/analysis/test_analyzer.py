import json
import tempfile
from pathlib import Path

import pytest

from prow_failure_analysis.analysis.analyzer import (
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


class TestParseArtifactFindings:
    """Tests for _parse_artifact_findings method."""

    def test_parse_valid_json_array(self, mocker):
        """Test parsing a valid JSON array of findings."""
        mocker.patch("prow_failure_analysis.analysis.analyzer.dspy")
        analyzer = FailureAnalyzer()

        raw = '[{"artifact_path": "pod.yaml", "key_findings": "Pod crashed"}]'
        findings = analyzer._parse_artifact_findings(raw, batch_num=1)

        assert len(findings) == 1
        assert findings[0]["artifact_path"] == "pod.yaml"
        assert findings[0]["key_findings"] == "Pod crashed"

    def test_parse_strips_markdown_json_block(self, mocker):
        """Test parsing strips ```json markdown wrapper."""
        mocker.patch("prow_failure_analysis.analysis.analyzer.dspy")
        analyzer = FailureAnalyzer()

        raw = '```json\n[{"artifact_path": "test.log", "key_findings": "Error found"}]\n```'
        findings = analyzer._parse_artifact_findings(raw, batch_num=1)

        assert len(findings) == 1
        assert findings[0]["artifact_path"] == "test.log"

    def test_parse_strips_plain_markdown_block(self, mocker):
        """Test parsing strips plain ``` markdown wrapper."""
        mocker.patch("prow_failure_analysis.analysis.analyzer.dspy")
        analyzer = FailureAnalyzer()

        raw = '```\n[{"artifact_path": "test.log", "key_findings": "OK"}]\n```'
        findings = analyzer._parse_artifact_findings(raw, batch_num=1)

        assert len(findings) == 1
        assert findings[0]["key_findings"] == "OK"

    def test_parse_empty_response_raises(self, mocker):
        """Test parsing empty response raises JSONDecodeError."""
        mocker.patch("prow_failure_analysis.analysis.analyzer.dspy")
        analyzer = FailureAnalyzer()

        with pytest.raises(json.JSONDecodeError):
            analyzer._parse_artifact_findings("", batch_num=1)

    def test_parse_whitespace_only_raises(self, mocker):
        """Test parsing whitespace-only response raises JSONDecodeError."""
        mocker.patch("prow_failure_analysis.analysis.analyzer.dspy")
        analyzer = FailureAnalyzer()

        with pytest.raises(json.JSONDecodeError):
            analyzer._parse_artifact_findings("   \n\t  ", batch_num=1)

    def test_parse_markdown_only_raises(self, mocker):
        """Test parsing markdown-only response raises JSONDecodeError."""
        mocker.patch("prow_failure_analysis.analysis.analyzer.dspy")
        analyzer = FailureAnalyzer()

        with pytest.raises(json.JSONDecodeError):
            analyzer._parse_artifact_findings("```json\n```", batch_num=1)

    def test_parse_non_array_raises(self, mocker):
        """Test parsing non-array JSON raises ValueError."""
        mocker.patch("prow_failure_analysis.analysis.analyzer.dspy")
        analyzer = FailureAnalyzer()

        with pytest.raises(ValueError, match="Expected JSON array"):
            analyzer._parse_artifact_findings('{"key": "value"}', batch_num=1)

    def test_parse_missing_artifact_path_raises(self, mocker):
        """Test parsing finding without artifact_path raises KeyError."""
        mocker.patch("prow_failure_analysis.analysis.analyzer.dspy")
        analyzer = FailureAnalyzer()

        with pytest.raises(KeyError, match="artifact_path"):
            analyzer._parse_artifact_findings('[{"key_findings": "test"}]', batch_num=1)

    def test_parse_missing_key_findings_raises(self, mocker):
        """Test parsing finding without key_findings raises KeyError."""
        mocker.patch("prow_failure_analysis.analysis.analyzer.dspy")
        analyzer = FailureAnalyzer()

        with pytest.raises(KeyError, match="key_findings"):
            analyzer._parse_artifact_findings('[{"artifact_path": "test.log"}]', batch_num=1)

    def test_parse_non_dict_finding_raises(self, mocker):
        """Test parsing non-dict finding raises ValueError."""
        mocker.patch("prow_failure_analysis.analysis.analyzer.dspy")
        analyzer = FailureAnalyzer()

        with pytest.raises(ValueError, match="not a dict"):
            analyzer._parse_artifact_findings('["string", "items"]', batch_num=1)

    def test_parse_sanitizes_control_chars(self, mocker):
        """Test parsing sanitizes control characters in strings."""
        mocker.patch("prow_failure_analysis.analysis.analyzer.dspy")
        analyzer = FailureAnalyzer()

        raw = '[{"artifact_path": "test.log", "key_findings": "error\nwith newlines"}]'
        findings = analyzer._parse_artifact_findings(raw, batch_num=1)

        assert "error" in findings[0]["key_findings"]
        assert "newlines" in findings[0]["key_findings"]

    def test_parse_multiple_findings(self, mocker):
        """Test parsing multiple findings in array."""
        mocker.patch("prow_failure_analysis.analysis.analyzer.dspy")
        analyzer = FailureAnalyzer()

        raw = """[
            {"artifact_path": "file1.yaml", "key_findings": "Finding 1"},
            {"artifact_path": "file2.json", "key_findings": "Finding 2"},
            {"artifact_path": "file3.log", "key_findings": "Finding 3"}
        ]"""
        findings = analyzer._parse_artifact_findings(raw, batch_num=1)

        assert len(findings) == 3
        assert findings[0]["artifact_path"] == "file1.yaml"
        assert findings[2]["key_findings"] == "Finding 3"


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
