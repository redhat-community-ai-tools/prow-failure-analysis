from prow_failure_analysis.analysis.analyzer import RCAReport, StepAnalysis
from prow_failure_analysis.output.github import (
    BOT_COMMENT_MARKER,
    _find_existing_bot_comment,
    post_pr_comment,
)


def _make_mock_pr(mocker, existing_comments=None):
    """Create a mock PR with optional existing comments."""
    mock_pr = mocker.Mock()
    mock_pr.get_issue_comments.return_value = existing_comments or []
    return mock_pr


def _setup_github_mocks(mocker, existing_comments=None):
    """Set up common GitHub mocks and return (mock_g, mock_pr)."""
    mocker.patch("prow_failure_analysis.output.github.Auth")
    mock_github = mocker.patch("prow_failure_analysis.output.github.Github")

    mock_g = mocker.Mock()
    mock_github.return_value = mock_g
    mock_repo = mocker.Mock()
    mock_g.get_repo.return_value = mock_repo
    mock_pr = _make_mock_pr(mocker, existing_comments)
    mock_repo.get_pull.return_value = mock_pr

    return mock_g, mock_pr


def _make_report(**overrides):
    """Create a default RCAReport with optional overrides."""
    defaults = dict(
        job_name="test-job",
        build_id="12345",
        pr_number="999",
        summary="Test failed",
        detailed_analysis="Details",
        category="test",
        step_analyses=[],
    )
    defaults.update(overrides)
    return RCAReport(**defaults)


class TestFindExistingBotComment:
    """Tests for finding existing bot comments on a PR."""

    def test_returns_matching_comment(self, mocker):
        """Returns the bot comment when one exists."""
        bot_comment = mocker.Mock()
        bot_comment.body = f"{BOT_COMMENT_MARKER}\n\nold analysis content"
        bot_comment.id = 42

        pr = _make_mock_pr(mocker, existing_comments=[bot_comment])

        assert _find_existing_bot_comment(pr) is bot_comment

    def test_returns_none_for_unrelated_comments(self, mocker):
        """Returns None when no bot comment exists."""
        other_comment = mocker.Mock()
        other_comment.body = "LGTM!"

        pr = _make_mock_pr(mocker, existing_comments=[other_comment])

        assert _find_existing_bot_comment(pr) is None

    def test_returns_none_when_no_comments(self, mocker):
        """Returns None when PR has zero comments."""
        pr = _make_mock_pr(mocker, existing_comments=[])
        assert _find_existing_bot_comment(pr) is None

    def test_returns_first_match_only(self, mocker):
        """Returns the first matching bot comment."""
        first = mocker.Mock()
        first.body = f"{BOT_COMMENT_MARKER}\nfirst"
        first.id = 1

        second = mocker.Mock()
        second.body = f"{BOT_COMMENT_MARKER}\nsecond"
        second.id = 2

        pr = _make_mock_pr(mocker, existing_comments=[first, second])

        assert _find_existing_bot_comment(pr) is first

    def test_skips_comment_with_none_body(self, mocker):
        """Comment with None body is safely skipped."""
        null_comment = mocker.Mock()
        null_comment.body = None

        pr = _make_mock_pr(mocker, existing_comments=[null_comment])

        assert _find_existing_bot_comment(pr) is None


class TestPostPRComment:
    """Tests for GitHub PR comment posting."""

    def test_org_repo_conversion(self, mocker):
        """Test org_repo converts from underscore to slash format."""
        mock_g, mock_pr = _setup_github_mocks(mocker)

        report = _make_report()
        post_pr_comment("fake-token", "kubernetes_kubernetes", 123, report)

        mock_g.get_repo.assert_called_once_with("kubernetes/kubernetes")

    def test_comment_body_includes_category(self, mocker):
        """Test comment body includes category."""
        _, mock_pr = _setup_github_mocks(mocker)

        report = _make_report(
            summary="Build failed", category="infrastructure"
        )
        post_pr_comment("fake-token", "org/repo", 123, report)

        call_args = mock_pr.create_issue_comment.call_args[0][0]
        assert "**Category:** Infrastructure" in call_args
        assert "Build failed" in call_args

    def test_comment_body_includes_step_evidence(self, mocker):
        """Test comment body includes step analyses."""
        _, mock_pr = _setup_github_mocks(mocker)

        step_analysis = StepAnalysis(
            step_name="build-stage/compile",
            failure_category="build",
            root_cause="Compilation failed",
            evidence=[
                {"source": "compile.log", "content": "Error: undefined symbol"},
                {"source": "linker.log", "content": "Error: missing header"},
            ],
        )

        report = _make_report(
            summary="Build failed",
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
        _, mock_pr = _setup_github_mocks(mocker)

        report = _make_report()
        post_pr_comment("fake-token", "org/repo", 123, report)

        call_args = mock_pr.create_issue_comment.call_args[0][0]
        assert "🔍 Failed Steps" not in call_args
        assert "Test failed" in call_args

    def test_github_client_closed(self, mocker):
        """Test GitHub client is properly closed."""
        mock_g, _ = _setup_github_mocks(mocker)

        report = _make_report()
        post_pr_comment("fake-token", "org/repo", 123, report)

        mock_g.close.assert_called_once()

    def test_updates_existing_comment_in_place(self, mocker):
        """Existing bot comment is edited in place, not deleted+recreated."""
        old_comment = mocker.Mock()
        old_comment.body = f"{BOT_COMMENT_MARKER}\n\nold analysis"
        old_comment.id = 99

        _, mock_pr = _setup_github_mocks(
            mocker, existing_comments=[old_comment]
        )

        report = _make_report()
        post_pr_comment("fake-token", "org/repo", 123, report)

        old_comment.edit.assert_called_once()
        mock_pr.create_issue_comment.assert_not_called()

    def test_creates_new_comment_when_none_exists(self, mocker):
        """New comment is created when no existing bot comment found."""
        _, mock_pr = _setup_github_mocks(mocker, existing_comments=[])

        report = _make_report()
        post_pr_comment("fake-token", "org/repo", 123, report)

        mock_pr.create_issue_comment.assert_called_once()
