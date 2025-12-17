import logging
import sys
import tempfile
from pathlib import Path

import click
import dspy

from .analysis.analyzer import FailureAnalyzer, RCAReport
from .config import Config
from .gcs.client import GCSClient
from .gcs.models import JobResult
from .output.github import post_pr_comment
from .processing.preprocessor import LogPreprocessor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def configure_dspy(config: Config) -> None:
    """Configure DSPy with the specified LLM."""
    logger.info(f"Configuring DSPy with {config.llm_provider}/{config.llm_model}")

    model = f"{config.llm_provider}/{config.llm_model}"
    lm_kwargs = {"model": model}

    if config.llm_api_key:
        lm_kwargs["api_key"] = config.llm_api_key

    if config.llm_base_url:
        lm_kwargs["api_base"] = config.llm_base_url
    elif config.llm_provider == "ollama":
        lm_kwargs["api_base"] = "http://localhost:11434"

    dspy.configure(lm=dspy.LM(**lm_kwargs))


@click.group()
@click.version_option()
def cli() -> None:
    """AI-powered pipeline failure analysis tool for OpenShift CI."""
    pass


def _setup_config(
    job_name: str | None,
    build_id: str | None,
    pr_number: str | None,
    org_repo: str | None,
    post_comment: bool,
) -> Config:
    """Setup and validate configuration."""
    config = Config()

    if job_name:
        config.job_name = job_name
    if build_id:
        config.build_id = build_id
    if pr_number:
        config.pr_number = pr_number
    if org_repo:
        config.org_repo = org_repo
    if post_comment:
        config.post_pr_comment = True

    errors = config.validate()
    if errors:
        logger.error("Configuration validation failed:")
        for error in errors:
            logger.error(f"  - {error}")
        sys.exit(1)

    return config


def _preprocess_logs(job_result: JobResult, preprocessor: LogPreprocessor, tokens_per_step: int) -> None:
    """Preprocess step logs in place."""
    logger.info("Preprocessing logs with cordon...")
    for step in job_result.failed_steps:
        if step.log_path:
            processed = preprocessor.preprocess_file(step.log_path, step.name, max_tokens=tokens_per_step)
            tmp_file = tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False)
            tmp_file.write(processed)
            tmp_file.close()
            Path(step.log_path).unlink(missing_ok=True)
            step.log_path = tmp_file.name


def _preprocess_artifacts(job_result: JobResult, preprocessor: LogPreprocessor, total_artifact_budget: int) -> None:
    """Preprocess additional artifacts with dynamic per-artifact budget."""
    if not job_result.additional_artifacts:
        return

    num_artifacts = len(job_result.additional_artifacts)
    # Reserve 20% of budget for JSON overhead, split rest among artifacts
    tokens_per_artifact = max(1_000, int(total_artifact_budget * 0.8) // num_artifacts)

    logger.info(f"Preprocessing {num_artifacts} artifacts (~{tokens_per_artifact:,} tokens each)")

    for artifact_path in job_result.additional_artifacts.keys():
        content = job_result.additional_artifacts[artifact_path]
        processed = preprocessor.preprocess(content, f"artifact:{artifact_path}", max_tokens=tokens_per_artifact)
        job_result.additional_artifacts[artifact_path] = processed


def _cleanup_temp_files(job_result: JobResult) -> None:
    """Clean up temporary log files."""
    for step in job_result.failed_steps:
        if step.log_path:
            Path(step.log_path).unlink(missing_ok=True)


def _post_to_github(config: Config, org_repo: str | None, report: RCAReport) -> None:
    """Post report to GitHub PR."""
    if not config.post_pr_comment:
        return

    if not config.pr_number or not org_repo:
        logger.warning("Cannot post PR comment: missing PR number or org/repo")
        return

    logger.info("Posting comment to PR...")
    try:
        post_pr_comment(
            github_token=config.github_token,  # type: ignore
            org_repo=org_repo,
            pr_number=int(config.pr_number),
            report=report,
        )
        logger.info("✅ Comment posted successfully")
    except Exception as e:
        logger.error(f"Failed to post PR comment: {e}")
        sys.exit(1)


@cli.command()
@click.option("--job-name", help="Prow job name (or set JOB_NAME env var)")
@click.option("--build-id", help="Build ID (or set BUILD_ID env var)")
@click.option("--pr-number", help="PR number for PR-triggered jobs (or set PULL_NUMBER env var)")
@click.option("--org-repo", help="Override org/repo (format: org_repo or org/repo)")
@click.option("--post-comment", is_flag=True, help="Post RCA as comment on originating PR")
@click.option("--verbose", is_flag=True, help="Enable verbose logging")
def analyze(
    job_name: str | None,
    build_id: str | None,
    pr_number: str | None,
    org_repo: str | None,
    post_comment: bool,
    verbose: bool,
) -> None:
    """Analyze a failed pipeline run and generate RCA report."""
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    config = _setup_config(job_name, build_id, pr_number, org_repo, post_comment)

    logger.info(f"Analyzing job: {config.job_name}, Build: {config.build_id}")
    if config.pr_number:
        logger.info(f"PR: #{config.pr_number}")

    configure_dspy(config)

    context_limit = config.detect_model_context_limit()
    logger.info(f"Model: {config.llm_model}, Context: {context_limit:,} tokens")

    gcs_client = GCSClient(config.gcs_bucket, config.gcs_creds_path, config=config)

    inferred_org_repo = config.infer_org_repo()
    if inferred_org_repo:
        logger.info(f"Using org_repo: {inferred_org_repo}")
    elif config.pr_number:
        logger.warning(f"Could not infer org_repo from {config.job_name}")

    logger.info("Fetching job results from GCS...")
    try:
        job_result = gcs_client.fetch_job_result(
            job_name=config.job_name,
            build_id=config.build_id,
            pr_number=config.pr_number,
            org_repo=inferred_org_repo,
        )
    except Exception as e:
        logger.error(f"Failed to fetch job result: {e}")
        sys.exit(1)

    if not job_result.failed_steps:
        logger.info("No failed steps found")
        print("\n✅ Job completed successfully - no failures to analyze.")
        sys.exit(0)

    num_failed_steps = len(job_result.failed_steps)
    num_failed_tests = len(job_result.failed_tests)
    num_artifacts = len(job_result.additional_artifacts)
    tokens_per_step, tokens_per_test, tokens_per_artifact_batch = config.calculate_token_budgets(
        num_failed_steps, num_failed_tests, num_artifacts
    )

    logger.info(f"Failures: {num_failed_steps} steps, {num_failed_tests} tests, {num_artifacts} artifacts")
    logger.info(
        f"Token budgets - steps: {tokens_per_step:,}, tests: {tokens_per_test:,}, "
        f"artifact_batch: {tokens_per_artifact_batch:,}"
    )

    preprocessor = LogPreprocessor(config=config)
    analyzer = FailureAnalyzer(
        preprocessor=preprocessor,
        config=config,
        tokens_per_step=tokens_per_step,
        tokens_per_test=tokens_per_test,
        tokens_per_artifact_batch=tokens_per_artifact_batch,
    )

    _preprocess_logs(job_result, preprocessor, tokens_per_step)
    _preprocess_artifacts(job_result, preprocessor, tokens_per_artifact_batch)

    logger.info("Analyzing failures with LLM...")
    try:
        report = analyzer.forward(job_result)
    except Exception as e:
        logger.error(f"Analysis failed: {e}")
        sys.exit(1)
    finally:
        _cleanup_temp_files(job_result)

    print("\n" + "=" * 80)
    print(report.to_markdown())
    print("=" * 80 + "\n")

    _post_to_github(config, inferred_org_repo, report)


if __name__ == "__main__":
    cli()
