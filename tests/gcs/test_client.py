import json
from datetime import datetime

from prow_failure_analysis.gcs.client import GCSClient


class TestGCSClient:
    """Tests for GCSClient parsing and filtering logic."""

    def test_parse_finished_json_success(self, mocker) -> None:
        """Test parsing a valid finished.json."""
        mocker.patch("prow_failure_analysis.gcs.client.storage")
        client = GCSClient(bucket_name="test-bucket")

        finished_json = json.dumps(
            {
                "timestamp": 1704110400,
                "passed": True,
                "result": "SUCCESS",
                "revision": "abc123",
                "metadata": {"job": "test-job"},
            }
        )

        result = client._parse_finished_json(finished_json)

        assert result is not None
        assert result.passed is True
        assert result.result == "SUCCESS"
        assert result.revision == "abc123"
        assert result.metadata == {"job": "test-job"}
        assert isinstance(result.timestamp, datetime)

    def test_parse_finished_json_minimal(self, mocker) -> None:
        """Test parsing finished.json with minimal fields."""
        mocker.patch("prow_failure_analysis.gcs.client.storage")
        client = GCSClient(bucket_name="test-bucket")

        finished_json = json.dumps({})

        result = client._parse_finished_json(finished_json)

        assert result is not None
        # Should use defaults
        assert result.passed is False
        assert result.result == "UNKNOWN"
        assert result.revision is None
        assert result.metadata is None

    def test_parse_finished_json_invalid(self, mocker) -> None:
        """Test parsing invalid JSON returns None."""
        mocker.patch("prow_failure_analysis.gcs.client.storage")
        client = GCSClient(bucket_name="test-bucket")

        result = client._parse_finished_json("not valid json {")

        assert result is None

    def test_verify_blob_exists_exception(self, mocker) -> None:
        """Test _verify_blob_exists handles exceptions gracefully."""
        mocker.patch("prow_failure_analysis.gcs.client.storage")
        client = GCSClient(bucket_name="test-bucket")

        client.bucket.blob = mocker.Mock(side_effect=Exception("Network error"))

        result = client._verify_blob_exists("test-path")

        assert result is False

    def test_fetch_blob_text_not_found(self, mocker) -> None:
        """Test _fetch_blob_text returns None for 404."""
        mocker.patch("prow_failure_analysis.gcs.client.storage")
        client = GCSClient(bucket_name="test-bucket")

        mock_blob = mocker.Mock()
        mock_blob.download_as_text.side_effect = Exception("404 Not Found")
        client.bucket.blob = mocker.Mock(return_value=mock_blob)

        result = client._fetch_blob_text("test-path")

        assert result is None

    def test_fetch_blob_text_other_error(self, mocker) -> None:
        """Test _fetch_blob_text returns None for other errors."""
        mocker.patch("prow_failure_analysis.gcs.client.storage")
        client = GCSClient(bucket_name="test-bucket")

        mock_blob = mocker.Mock()
        mock_blob.download_as_text.side_effect = Exception("Network error")
        client.bucket.blob = mocker.Mock(return_value=mock_blob)

        result = client._fetch_blob_text("test-path")

        assert result is None

    def test_fetch_finished_json_not_found(self, mocker) -> None:
        """Test _fetch_finished_json returns None when file not found."""
        mocker.patch("prow_failure_analysis.gcs.client.storage")
        client = GCSClient(bucket_name="test-bucket")

        client._fetch_blob_text = mocker.Mock(return_value=None)

        result = client._fetch_finished_json("base/path")

        assert result is None

    def test_fetch_step_graph_success(self, mocker) -> None:
        """Test _fetch_step_graph successfully fetches and parses JSON."""
        mocker.patch("prow_failure_analysis.gcs.client.storage")
        client = GCSClient(bucket_name="test-bucket")

        step_graph_content = json.dumps({"nodes": ["step1", "step2"], "edges": []})

        client._fetch_blob_text = mocker.Mock(return_value=step_graph_content)

        result = client._fetch_step_graph("base/path")

        assert result == {"nodes": ["step1", "step2"], "edges": []}
        client._fetch_blob_text.assert_called_once_with("base/path/artifacts/ci-operator-step-graph.json")

    def test_fetch_step_graph_not_found(self, mocker) -> None:
        """Test _fetch_step_graph returns empty dict when file not found."""
        mocker.patch("prow_failure_analysis.gcs.client.storage")
        client = GCSClient(bucket_name="test-bucket")

        client._fetch_blob_text = mocker.Mock(return_value=None)

        result = client._fetch_step_graph("base/path")

        assert result == {}

    def test_fetch_step_graph_invalid_json(self, mocker) -> None:
        """Test _fetch_step_graph returns empty dict for invalid JSON."""
        mocker.patch("prow_failure_analysis.gcs.client.storage")
        client = GCSClient(bucket_name="test-bucket")

        client._fetch_blob_text = mocker.Mock(return_value="invalid json {")

        result = client._fetch_step_graph("base/path")

        assert result == {}

    def test_list_xunit_files_filters_by_pattern(self, mocker) -> None:
        """Test _list_xunit_files filters files by expected patterns."""
        mocker.patch("prow_failure_analysis.gcs.client.storage")
        client = GCSClient(bucket_name="test-bucket")

        # Mock blobs - need to set name as an attribute
        def create_blob(path: str):
            blob = mocker.Mock()
            blob.name = path
            return blob

        mock_blobs = [
            create_blob("base/artifacts/stage/step/junit.xml"),
            create_blob("base/artifacts/stage/step/junit-results.xml"),
            create_blob("base/artifacts/stage/step/e2e-report.xml"),
            create_blob("base/artifacts/stage/step/results/test-results.xml"),
            create_blob("base/artifacts/stage/step/test-results/output.xml"),
            create_blob("base/artifacts/stage/step/random-file.txt"),  # Should be ignored
            create_blob("base/artifacts/stage/step/data.xml"),  # Should be ignored (no pattern match)
        ]

        client.client.list_blobs = mocker.Mock(return_value=mock_blobs)
        client._verify_blob_exists = mocker.Mock(return_value=True)

        result = client._list_xunit_files("base")

        # Should include files matching patterns: junit, report, results, test-results
        assert len(result) == 5
        assert "base/artifacts/stage/step/junit.xml" in result
        assert "base/artifacts/stage/step/junit-results.xml" in result
        assert "base/artifacts/stage/step/e2e-report.xml" in result
        assert "base/artifacts/stage/step/results/test-results.xml" in result
        assert "base/artifacts/stage/step/test-results/output.xml" in result

    def test_list_xunit_files_respects_config_filter(self, mocker) -> None:
        """Test _list_xunit_files respects config step filtering."""
        mocker.patch("prow_failure_analysis.gcs.client.storage")
        mock_config = mocker.Mock()
        mock_config.should_ignore_step.side_effect = lambda step: step == "stage/filtered-step"

        client = GCSClient(bucket_name="test-bucket", config=mock_config)

        # Mock blobs - need to set name as an attribute
        def create_blob(path: str):
            blob = mocker.Mock()
            blob.name = path
            return blob

        mock_blobs = [
            create_blob("base/artifacts/stage/allowed-step/junit.xml"),
            create_blob("base/artifacts/stage/filtered-step/junit.xml"),  # Should be filtered
        ]

        client.client.list_blobs = mocker.Mock(return_value=mock_blobs)
        client._verify_blob_exists = mocker.Mock(return_value=True)

        result = client._list_xunit_files("base")

        # Should only include the allowed step
        assert len(result) == 1
        assert "base/artifacts/stage/allowed-step/junit.xml" in result
