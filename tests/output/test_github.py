from prow_failure_analysis.analysis.analyzer import RCAReport, StepAnalysis
from prow_failure_analysis.output.github import post_pr_comment


class TestPostPRComment:
    """Tests for GitHub PR comment posting."""

    def test_org_repo_conversion(self, mocker):
        """Test org_repo converts from underscore to slash format."""
        mocker.patch("prow_failure_analysis.output.github.Auth")
        mock_github = mocker.patch("prow_failure_analysis.output.github.Github")

        mock_g = mocker.Mock()
        mock_github.return_value = mock_g
        mock_repo = mocker.Mock()
        mock_g.get_repo.return_value = mock_repo
        mock_pr = mocker.Mock()
        mock_repo.get_pull.return_value = mock_pr

        report = RCAReport(
            job_name="test-job",
            build_id="12345",
            pr_number="999",
            summary="Test failed",
            detailed_analysis="Details",
            category="test",
            step_analyses=[],
        )

        post_pr_comment("fake-token", "kubernetes_kubernetes", 123, report)

        mock_g.get_repo.assert_called_once_with("kubernetes/kubernetes")

    def test_comment_body_includes_category(self, mocker):
        """Test comment body includes category."""
        mocker.patch("prow_failure_analysis.output.github.Auth")
        mock_github = mocker.patch("prow_failure_analysis.output.github.Github")

        mock_g = mocker.Mock()
        mock_github.return_value = mock_g
        mock_repo = mocker.Mock()
        mock_g.get_repo.return_value = mock_repo
        mock_pr = mocker.Mock()
        mock_repo.get_pull.return_value = mock_pr

        report = RCAReport(
            job_name="test-job",
            build_id="12345",
            pr_number="999",
            summary="Build failed",
            detailed_analysis="Details",
            category="infrastructure",
            step_analyses=[],
        )

        post_pr_comment("fake-token", "org/repo", 123, report)

        call_args = mock_pr.create_issue_comment.call_args[0][0]
        assert "**Category:** Infrastructure" in call_args
        assert "Build failed" in call_args

    def test_comment_body_includes_step_evidence(self, mocker):
        """Test comment body includes step analyses."""
        mocker.patch("prow_failure_analysis.output.github.Auth")
        mock_github = mocker.patch("prow_failure_analysis.output.github.Github")

        mock_g = mocker.Mock()
        mock_github.return_value = mock_g
        mock_repo = mocker.Mock()
        mock_g.get_repo.return_value = mock_repo
        mock_pr = mocker.Mock()
        mock_repo.get_pull.return_value = mock_pr

        step_analysis = StepAnalysis(
            step_name="build-stage/compile",
            failure_category="build",
            root_cause="Compilation failed",
            evidence=["Error: undefined symbol", "Error: missing header"],
        )

        report = RCAReport(
            job_name="test-job",
            build_id="12345",
            pr_number="999",
            summary="Build failed",
            detailed_analysis="Details",
            category="build",
            step_analyses=[step_analysis],
        )

        post_pr_comment("fake-token", "org/repo", 123, report)

        call_args = mock_pr.create_issue_comment.call_args[0][0]
        assert "build-stage/compile" in call_args
        assert "Compilation failed" in call_args
        assert "Error: undefined symbol" in call_args

    def test_comment_body_no_steps(self, mocker):
        """Test comment body works with no step analyses."""
        mocker.patch("prow_failure_analysis.output.github.Auth")
        mock_github = mocker.patch("prow_failure_analysis.output.github.Github")

        mock_g = mocker.Mock()
        mock_github.return_value = mock_g
        mock_repo = mocker.Mock()
        mock_g.get_repo.return_value = mock_repo
        mock_pr = mocker.Mock()
        mock_repo.get_pull.return_value = mock_pr

        report = RCAReport(
            job_name="test-job",
            build_id="12345",
            pr_number="999",
            summary="Test failed",
            detailed_analysis="Details",
            category="test",
            step_analyses=[],
        )

        post_pr_comment("fake-token", "org/repo", 123, report)

        call_args = mock_pr.create_issue_comment.call_args[0][0]
        assert "🔍 Failed Steps" not in call_args
        assert "Test failed" in call_args

    def test_github_client_closed(self, mocker):
        """Test GitHub client is properly closed."""
        mocker.patch("prow_failure_analysis.output.github.Auth")
        mock_github = mocker.patch("prow_failure_analysis.output.github.Github")

        mock_g = mocker.Mock()
        mock_github.return_value = mock_g
        mock_repo = mocker.Mock()
        mock_g.get_repo.return_value = mock_repo
        mock_pr = mocker.Mock()
        mock_repo.get_pull.return_value = mock_pr

        report = RCAReport(
            job_name="test-job",
            build_id="12345",
            pr_number="999",
            summary="Test failed",
            detailed_analysis="Details",
            category="test",
            step_analyses=[],
        )

        post_pr_comment("fake-token", "org/repo", 123, report)

        mock_g.close.assert_called_once()
