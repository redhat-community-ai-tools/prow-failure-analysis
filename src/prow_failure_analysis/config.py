import fnmatch
import logging
import os
from dataclasses import dataclass, field

from litellm import model_cost

logger = logging.getLogger(__name__)


@dataclass
class Config:
    """Configuration for the failure analyzer."""

    gcs_bucket: str = field(default_factory=lambda: os.getenv("GCS_BUCKET", "test-platform-results"))
    gcs_creds_path: str | None = field(default_factory=lambda: os.getenv("GCS_CREDS_PATH"))

    job_name: str = field(default_factory=lambda: os.getenv("JOB_NAME", ""))
    build_id: str = field(default_factory=lambda: os.getenv("BUILD_ID", ""))
    pr_number: str | None = field(default_factory=lambda: os.getenv("PULL_NUMBER"))
    org_repo: str | None = field(default_factory=lambda: os.getenv("ORG_REPO"))

    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", ""))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", ""))
    llm_api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", ""))
    llm_base_url: str | None = field(default_factory=lambda: os.getenv("LLM_BASE_URL"))

    cordon_device: str = field(default_factory=lambda: os.getenv("CORDON_DEVICE", "cpu"))

    github_token: str | None = field(default_factory=lambda: os.getenv("GITHUB_TOKEN"))
    post_pr_comment: bool = False

    ignored_steps_patterns: list[str] = field(
        default_factory=lambda: os.getenv("IGNORED_STEPS", "").split(",") if os.getenv("IGNORED_STEPS") else []
    )

    included_artifacts_patterns: list[str] = field(
        default_factory=lambda: (
            os.getenv("INCLUDED_ARTIFACTS", "").split(",") if os.getenv("INCLUDED_ARTIFACTS") else []
        )
    )

    def validate(self) -> list[str]:
        """Validate configuration and return list of errors."""
        errors = []

        if not self.job_name:
            errors.append("JOB_NAME is required")
        if not self.build_id:
            errors.append("BUILD_ID is required")
        if not self.llm_provider:
            errors.append("LLM_PROVIDER is required")
        if not self.llm_model:
            errors.append("LLM_MODEL is required")
        if not self.llm_api_key:
            errors.append("LLM_API_KEY is required")
        if self.post_pr_comment and not self.github_token:
            errors.append("GITHUB_TOKEN is required when --post-comment is enabled")

        return errors

    def _check_github_repo_exists(self, org: str, repo: str) -> bool:
        """Check if a GitHub repository exists."""
        if not self.github_token:
            return False

        try:
            from github import Auth, Github

            auth = Auth.Token(self.github_token)
            g = Github(auth=auth)
            try:
                g.get_repo(f"{org}/{repo}")
                return True
            except Exception:
                return False
            finally:
                g.close()
        except Exception as e:
            logger.debug(f"GitHub check failed: {e}")
            return False

    def _extract_org_repo_section(self, job_name: str) -> str | None:
        """Extract the org-repo section from job name."""
        parts = job_name.split("-")

        if parts[0] == "rehearse":
            parts = parts[2:]

        if len(parts) < 5 or not (parts[0] in ["pull", "periodic"] and parts[1] == "ci"):
            return None

        after_prefix = "-".join(parts[2:])
        branch_indicators = ["-main-", "-master-", "-release-", "-develop-"]

        for indicator in branch_indicators:
            if indicator in after_prefix:
                return after_prefix.split(indicator)[0]

        return None

    def _find_valid_org_repo_split(self, org_repo_section: str) -> str | None:
        """Try different dash splits and validate against GitHub."""
        dashes = [i for i, c in enumerate(org_repo_section) if c == "-"]
        if not dashes:
            return None

        if not self.github_token:
            logger.warning(
                f"Cannot validate org/repo '{org_repo_section}' - no GitHub token set. "
                "Set GITHUB_TOKEN env var or use ORG_REPO/--org-repo to explicitly specify."
            )
            first_dash = dashes[0]
            return f"{org_repo_section[:first_dash]}_{org_repo_section[first_dash + 1:]}"

        for dash_idx in dashes:
            org = org_repo_section[:dash_idx]
            repo = org_repo_section[dash_idx + 1 :]

            if self._check_github_repo_exists(org, repo):
                logger.info(f"Validated repo on GitHub: {org}/{repo}")
                return f"{org}_{repo}"

        logger.warning(
            f"Could not validate '{org_repo_section}' against GitHub. "
            "Use ORG_REPO env var or --org-repo flag to explicitly specify."
        )
        first_dash = dashes[0]
        return f"{org_repo_section[:first_dash]}_{org_repo_section[first_dash + 1:]}"

    def infer_org_repo(self) -> str | None:
        """Infer org_repo from job name by trying different splits and validating against GitHub.

        For edge cases, set ORG_REPO env var or use --org-repo CLI option.
        """
        if self.org_repo:
            return self.org_repo.replace("/", "_")

        if not self.job_name:
            return None

        org_repo_section = self._extract_org_repo_section(self.job_name)
        if org_repo_section:
            return self._find_valid_org_repo_split(org_repo_section)

        return None

    def detect_model_context_limit(self) -> int:
        """Query model's context window from LiteLLM database."""
        try:
            full_model = f"{self.llm_provider}/{self.llm_model}"

            if full_model in model_cost:
                max_input: int | None = model_cost[full_model].get("max_input_tokens")
                if max_input:
                    logger.info(f"Detected context for {full_model}: {max_input:,} tokens")
                    return max_input

            if self.llm_model in model_cost:
                max_input = model_cost[self.llm_model].get("max_input_tokens")
                if max_input:
                    logger.info(f"Detected context for {self.llm_model}: {max_input:,} tokens")
                    return max_input

            for model_key in model_cost.keys():
                if self.llm_model in model_key or model_key in self.llm_model:
                    max_input = model_cost[model_key].get("max_input_tokens")
                    if max_input:
                        logger.info(f"Detected context for {model_key}: {max_input:,} tokens")
                        return max_input

            logger.warning(f"Model {self.llm_model} not in database, using 128K default")
            return 128000

        except Exception as e:
            logger.warning(f"Error querying context limit: {e}, using 128K default")
            return 128000

    def calculate_token_budgets(
        self, num_failed_steps: int, num_failed_tests: int, num_artifacts: int
    ) -> tuple[int, int, int]:
        """Calculate dynamic token budgets based on number of failures.

        Returns:
            Tuple of (tokens_per_step, tokens_per_test, tokens_per_artifact_batch)

        Note: tokens_per_artifact_batch is the TOTAL budget for all artifacts in a batch,
        not per artifact. Artifacts are batched to reduce API overhead.
        """
        context_limit = self.detect_model_context_limit()
        available = context_limit - int(context_limit * 0.15)

        if num_failed_steps == 0 and num_failed_tests == 0 and num_artifacts == 0:
            return (int(context_limit * 0.20), int(context_limit * 0.08), int(context_limit * 0.08))

        # Weight: steps=2x, tests=1x, artifacts=1x total (not per artifact)
        total_units = (num_failed_steps * 2) + num_failed_tests + 1  # 1 unit for all artifacts
        tokens_per_unit = available // total_units

        tokens_per_step = max(10_000, min(200_000, tokens_per_unit * 2))
        tokens_per_test = max(10_000, min(80_000, tokens_per_unit))
        tokens_per_artifact_batch = max(20_000, min(150_000, tokens_per_unit))  # Total for all artifacts

        return (tokens_per_step, tokens_per_test, tokens_per_artifact_batch)

    def should_ignore_step(self, step_name: str) -> bool:
        """Check if step matches any ignore pattern."""
        return any(
            fnmatch.fnmatch(step_name, pattern.strip()) for pattern in self.ignored_steps_patterns if pattern.strip()
        )

    def should_include_artifact_path(self, artifact_path: str) -> bool:
        """Check if artifact path matches any include pattern."""
        return any(
            fnmatch.fnmatch(artifact_path, pattern.strip())
            for pattern in self.included_artifacts_patterns
            if pattern.strip()
        )
