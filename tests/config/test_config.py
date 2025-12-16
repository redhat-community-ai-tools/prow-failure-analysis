"""Unit tests for configuration module."""

from prow_failure_analysis.config import Config


class TestConfig:
    """Tests for Config custom logic."""

    def test_validate_missing_required_fields(self):
        """Test validate returns errors for missing required fields."""
        config = Config()

        errors = config.validate()

        assert "JOB_NAME is required" in errors
        assert "BUILD_ID is required" in errors
        assert "LLM_PROVIDER is required" in errors
        assert "LLM_MODEL is required" in errors
        assert "LLM_API_KEY is required" in errors

    def test_validate_with_all_fields(self):
        """Test validate returns no errors when all required fields set."""
        config = Config()
        config.job_name = "test-job"
        config.build_id = "12345"
        config.llm_provider = "openai"
        config.llm_model = "gpt-4"
        config.llm_api_key = "fake-key"

        errors = config.validate()

        assert errors == []

    def test_validate_post_comment_requires_github_token(self):
        """Test validate requires github_token when post_pr_comment is enabled."""
        config = Config()
        config.job_name = "test-job"
        config.build_id = "12345"
        config.llm_provider = "openai"
        config.llm_model = "gpt-4"
        config.llm_api_key = "fake-key"
        config.post_pr_comment = True

        errors = config.validate()

        assert "GITHUB_TOKEN is required when --post-comment is enabled" in errors

    def test_should_ignore_step_no_patterns(self):
        """Test should_ignore_step returns False when no patterns configured."""
        config = Config()

        assert config.should_ignore_step("test-step") is False

    def test_should_ignore_step_matches_pattern(self):
        """Test should_ignore_step returns True for matching patterns."""
        config = Config()
        config.ignored_steps_patterns = ["*-report", "gather-*"]

        assert config.should_ignore_step("e2e-report") is True
        assert config.should_ignore_step("gather-must-gather") is True
        assert config.should_ignore_step("test-step") is False

    def test_should_include_artifact_path_no_patterns(self):
        """Test should_include_artifact_path returns False when no patterns configured."""
        config = Config()

        assert config.should_include_artifact_path("test/file.txt") is False

    def test_should_include_artifact_path_matches_pattern(self):
        """Test should_include_artifact_path returns True for matching patterns."""
        config = Config()
        config.included_artifacts_patterns = ["*/gather/*", "*.yaml"]

        assert config.should_include_artifact_path("test/gather/cluster.yaml") is True
        assert config.should_include_artifact_path("config.yaml") is True
        assert config.should_include_artifact_path("test/random.txt") is False

    def test_calculate_token_budgets_steps_weighted_higher(self):
        """Test steps get 2x weight compared to tests."""
        config = Config()
        config.llm_model = "gpt-4"
        config.llm_provider = "openai"

        tokens_per_step, tokens_per_test = config.calculate_token_budgets(1, 1)

        assert tokens_per_step > 0
        assert tokens_per_test > 0
        assert tokens_per_step >= tokens_per_test

    def test_calculate_token_budgets_enforces_limits(self):
        """Test token budgets enforce min/max limits."""
        config = Config()
        config.llm_model = "gpt-4"
        config.llm_provider = "openai"

        tokens_per_step, tokens_per_test = config.calculate_token_budgets(1, 1)

        assert tokens_per_step >= 10_000
        assert tokens_per_test >= 10_000
        assert tokens_per_step <= 200_000
        assert tokens_per_test <= 80_000

    def test_extract_org_repo_section_valid_job(self):
        """Test extracting org-repo section from valid job name."""
        config = Config()

        section = config._extract_org_repo_section("pull-ci-windup-windup-ui-tests-main-test")

        assert section == "windup-windup-ui-tests"

    def test_extract_org_repo_section_rehearse_pull_job(self):
        """Test extracting org-repo section from rehearse pull job."""
        config = Config()

        section = config._extract_org_repo_section("rehearse-12345-pull-ci-org-repo-main-test")

        assert section == "org-repo"

    def test_extract_org_repo_section_rehearse_periodic_job(self):
        """Test extracting org-repo section from rehearse periodic job."""
        config = Config()

        job = "rehearse-70935-periodic-ci-stolostron-policy-collection-main-ocp4.20-interop-opp-aws"
        section = config._extract_org_repo_section(job)

        assert section == "stolostron-policy-collection"

    def test_extract_org_repo_section_invalid_job(self):
        """Test extracting org-repo returns None for invalid job names."""
        config = Config()

        assert config._extract_org_repo_section("invalid-job-name") is None
        assert config._extract_org_repo_section("periodic-job") is None

    def test_find_valid_org_repo_split_first_dash(self):
        """Test finding org/repo split uses first dash as fallback."""
        config = Config()

        result = config._find_valid_org_repo_split("windup-windup-ui-tests")

        assert result == "windup_windup-ui-tests"

    def test_infer_org_repo_with_explicit_config(self):
        """Test infer_org_repo uses explicit config when set."""
        config = Config()
        config.org_repo = "my-org/my-repo"

        result = config.infer_org_repo()

        assert result == "my-org_my-repo"

    def test_infer_org_repo_no_job_name(self):
        """Test infer_org_repo returns None when job_name not set."""
        config = Config()

        result = config.infer_org_repo()

        assert result is None
