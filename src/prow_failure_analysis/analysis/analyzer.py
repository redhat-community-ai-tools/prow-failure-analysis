import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import dspy

from ..gcs.models import JobResult, StepResult
from ..parsing.xunit_models import FailedTest
from ..security.leak_detector import LeakDetector
from .signatures import AnalyzeArtifacts, AnalyzeStepFailure, AnalyzeTestFailure, GenerateRCA

logger = logging.getLogger(__name__)

# Retry configuration for transient LLM failures
MAX_RETRIES = 3
RETRY_BASE_DELAY = 2.0  # seconds


def _estimate_tokens(text: str) -> int:
    """Rough token estimation (chars / 4)."""
    return len(text) // 4


@dataclass
class StepAnalysis:
    """Analysis result for a single step."""

    step_name: str
    failure_category: str
    root_cause: str
    evidence: list[dict[str, str]]  # List of {"source": "path", "content": "log excerpt"}


@dataclass
class TestFailureAnalysis:
    """Analysis result for a single test failure."""

    test_identifier: str
    source_file: str
    root_cause_summary: str


@dataclass
class ArtifactAnalysis:
    """Analysis result for a diagnostic artifact."""

    artifact_path: str
    key_findings: str


@dataclass
class RCAReport:
    """Complete root cause analysis report."""

    job_name: str
    build_id: str
    pr_number: str | None
    summary: str
    detailed_analysis: str
    category: str
    step_analyses: list[StepAnalysis]
    test_analyses: list[TestFailureAnalysis] = field(default_factory=list)
    artifact_analyses: list[ArtifactAnalysis] = field(default_factory=list)

    def to_markdown(self) -> str:
        """Generate markdown formatted report with leak detection."""
        parts = [
            "# Pipeline Failure Analysis\n",
            f"**Job:** `{self.job_name}`\n",
            f"**Build:** `{self.build_id}`",
        ]

        if self.pr_number:
            parts.append(f" | **PR:** #{self.pr_number}")
        parts.append(f" | **Category:** {self.category.title()}")

        parts.extend(["\n\n---\n", "## Root Cause\n\n", f"{self.summary}\n\n"])
        parts.extend(["## Technical Details\n\n", f"{self.detailed_analysis}\n\n"])

        if self.step_analyses:
            parts.append("## Evidence\n\n")
            for analysis in self.step_analyses:
                parts.append(f"**{analysis.step_name}** — *{analysis.failure_category}*\n\n")
                if analysis.evidence:
                    for item in analysis.evidence:
                        # Evidence is now a dict with 'source' and 'content'
                        source = item.get("source", "unknown")
                        content = item.get("content", "").replace("`", "'").strip()

                        # Add source label
                        parts.append(f"**{source}:**\n\n")

                        # Format content based on length/structure
                        if "\n" in content or len(content) > 100:
                            parts.append(f"```\n{content}\n```\n\n")
                        else:
                            parts.append(f"`{content}`\n\n")

        markdown_output = "".join(parts)

        # Sanitize the entire markdown output to prevent secret leaks
        leak_detector = LeakDetector()
        sanitized_output = leak_detector.sanitize_text(markdown_output)

        return sanitized_output


class FailureAnalyzer(dspy.Module):
    """DSPy module for analyzing pipeline failures."""

    def __init__(
        self,
        preprocessor: Any = None,
        config: Any = None,
        tokens_per_step: int = 100_000,
        tokens_per_test: int = 50_000,
        tokens_per_artifact_batch: int = 50_000,
    ) -> None:
        """Initialize the analyzer.

        Args:
            preprocessor: LogPreprocessor for reducing test details
            config: Config for token limits
            tokens_per_step: Token limit per step
            tokens_per_test: Token limit per test
            tokens_per_artifact_batch: Total token budget per artifact batch
        """
        super().__init__()
        self.step_analyzer = dspy.ChainOfThought(AnalyzeStepFailure)
        self.test_analyzer = dspy.ChainOfThought(AnalyzeTestFailure)
        # Use Predict for artifacts - simpler and more stable for JSON output
        self.artifact_analyzer = dspy.Predict(AnalyzeArtifacts)
        self.rca_generator = dspy.ChainOfThought(GenerateRCA)
        self.preprocessor = preprocessor
        self.config = config
        self.tokens_per_step = tokens_per_step
        self.tokens_per_test = tokens_per_test
        self.tokens_per_artifact_batch = tokens_per_artifact_batch

    def _get_step_context(self, step: StepResult, step_graph: dict[str, Any]) -> str:
        """Extract step context from the step graph."""
        if not step_graph:
            return f"Step {step.name} - no graph information available"

        try:
            nodes = step_graph.get("nodes", [])
            step_short_name = step.name.split("/")[-1]
            matching_nodes = [n for n in nodes if step_short_name in n.get("name", "")]

            if matching_nodes:
                deps = matching_nodes[0].get("dependencies", [])
                return f"Step {step.name} - dependencies: {deps}"

            return f"Step {step.name} - part of pipeline execution"
        except Exception as e:
            logger.debug(f"Failed to extract step context: {e}")
            return f"Step {step.name}"

    def _read_log_content(self, step: StepResult) -> str:
        """Read log content from step's temp file."""
        if not step.log_path:
            return "(No log content available)"

        try:
            return Path(step.log_path).read_text()
        except Exception as e:
            logger.error(f"Failed to read log from {step.log_path}: {e}")
            return "(No log content available)"

    def _analyze_step(
        self, step: StepResult, step_graph: dict[str, Any], max_retries: int = MAX_RETRIES
    ) -> StepAnalysis:
        """Analyze a single failed step with retry logic."""
        logger.info(f"Analyzing step: {step.name}")

        step_context = self._get_step_context(step, step_graph)
        log_content = self._read_log_content(step)

        last_error: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                result = self.step_analyzer(
                    step_name=step.name,
                    log_content=log_content,
                    step_context=step_context,
                )

                # Parse evidence JSON
                evidence_list = []
                try:
                    evidence_list = json.loads(result.evidence)
                    if not isinstance(evidence_list, list):
                        evidence_list = []
                except (json.JSONDecodeError, TypeError) as e:
                    logger.warning(f"Failed to parse evidence JSON for {step.name}: {e}")
                    evidence_list = []

                return StepAnalysis(
                    step_name=step.name,
                    failure_category=result.failure_category,
                    root_cause=result.root_cause,
                    evidence=evidence_list,
                )
            except (json.JSONDecodeError, KeyError) as e:
                last_error = e
                if attempt < max_retries:
                    delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    logger.warning(
                        f"Step {step.name} attempt {attempt}/{max_retries} failed: {e}. " f"Retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)
                else:
                    logger.error(f"Step {step.name} failed after {max_retries} attempts: {e}")
            except Exception as e:
                logger.error(f"Step {step.name}: analysis failed: {e}")
                last_error = e
                break

        return StepAnalysis(
            step_name=step.name,
            failure_category="unknown",
            root_cause=f"Analysis failed: {str(last_error)}",
            evidence=[],
        )

    def _analyze_test_failure(self, test: FailedTest, max_retries: int = MAX_RETRIES) -> TestFailureAnalysis:
        """Analyze a single test failure with retry logic."""
        logger.info(f"Analyzing test: {test.test_identifier}")

        details = test.combined_details
        if self.preprocessor:
            details = self.preprocessor.preprocess(
                details, f"test:{test.test_identifier}", max_tokens=self.tokens_per_test
            )

        last_error: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                result = self.test_analyzer(
                    test_identifier=test.test_identifier,
                    failure_type=test.failure_type or test.error_type or "Unknown",
                    failure_message=test.failure_message or test.error_message or "No message",
                    failure_details=details,
                )

                return TestFailureAnalysis(
                    test_identifier=test.test_identifier,
                    source_file=test.source_file,
                    root_cause_summary=result.root_cause_summary,
                )
            except (json.JSONDecodeError, KeyError) as e:
                last_error = e
                if attempt < max_retries:
                    delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    logger.warning(
                        f"Test {test.test_identifier} attempt {attempt}/{max_retries} failed: {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)
                else:
                    logger.error(f"Test {test.test_identifier} failed after {max_retries} attempts: {e}")
            except Exception as e:
                logger.error(f"Test {test.test_identifier}: analysis failed: {e}")
                last_error = e
                break

        return TestFailureAnalysis(
            test_identifier=test.test_identifier,
            source_file=test.source_file,
            root_cause_summary=f"Analysis failed: {str(last_error)}",
        )

    def _analyze_all_test_failures(self, tests: list[FailedTest]) -> list[TestFailureAnalysis]:
        """Analyze all test failures."""
        if not tests:
            return []

        logger.info(f"Analyzing {len(tests)} test failures")
        return [self._analyze_test_failure(test) for test in tests]

    def _batch_artifacts_by_tokens(self, artifacts: dict[str, str], max_tokens: int) -> list[dict[str, str]]:
        """Split artifacts into batches that fit within token budget.

        Args:
            artifacts: Dict of artifact_path -> content
            max_tokens: Maximum tokens per batch (includes JSON overhead)

        Returns:
            List of artifact batches, each a dict of path -> content
        """
        batches: list[dict[str, str]] = []
        current_batch: dict[str, str] = {}
        current_tokens = 100  # JSON overhead

        for path, content in artifacts.items():
            artifact_tokens = _estimate_tokens(path) + _estimate_tokens(content) + 50  # JSON overhead

            # If adding this artifact would exceed limit, start new batch
            if current_tokens + artifact_tokens > max_tokens and current_batch:
                batches.append(current_batch)
                current_batch = {}
                current_tokens = 100

            current_batch[path] = content
            current_tokens += artifact_tokens

        if current_batch:
            batches.append(current_batch)

        return batches

    def _parse_artifact_findings(self, raw_findings: str, batch_num: int) -> list[dict[str, str]]:
        """Parse and validate artifact findings JSON with fallback handling."""
        if not raw_findings or not raw_findings.strip():
            raise json.JSONDecodeError("Empty response from LLM", "", 0)

        # Clean up common LLM response issues
        cleaned = raw_findings.strip()

        # Remove markdown code blocks if present
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        if not cleaned:
            raise json.JSONDecodeError("Response contained only markdown formatting", "", 0)

        findings_list = json.loads(cleaned)

        if not isinstance(findings_list, list):
            raise ValueError(f"Expected JSON array, got {type(findings_list).__name__}")

        # Validate each finding has required keys
        for i, finding in enumerate(findings_list):
            if not isinstance(finding, dict):
                raise ValueError(f"Finding {i} is not a dict: {type(finding).__name__}")
            if "artifact_path" not in finding:
                raise KeyError(f"Finding {i} missing 'artifact_path'")
            if "key_findings" not in finding:
                raise KeyError(f"Finding {i} missing 'key_findings'")

        return findings_list

    def _analyze_artifact_batch(
        self, batch: dict[str, str], batch_num: int, max_retries: int = MAX_RETRIES
    ) -> list[ArtifactAnalysis]:
        """Analyze a batch of artifacts together with retry logic."""
        if not batch:
            return []

        logger.info(f"Analyzing artifact batch {batch_num} ({len(batch)} artifacts)")
        artifacts_json = json.dumps(batch, indent=2)

        last_error: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                result = self.artifact_analyzer(artifacts_json=artifacts_json)

                # Get the raw response and log it for debugging if it fails
                raw_findings = getattr(result, "artifact_findings", None)
                if raw_findings is None:
                    raise ValueError("No artifact_findings in response")

                # Parse and validate the JSON output
                findings_list = self._parse_artifact_findings(raw_findings, batch_num)

                return [
                    ArtifactAnalysis(
                        artifact_path=finding["artifact_path"],
                        key_findings=finding["key_findings"],
                    )
                    for finding in findings_list
                ]
            except (KeyError, ValueError) as e:  # ValueError includes JSONDecodeError
                last_error = e
                if attempt < max_retries:
                    delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))  # Exponential backoff
                    logger.warning(
                        f"Artifact batch {batch_num} attempt {attempt}/{max_retries} failed: {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)
                else:
                    logger.error(f"Artifact batch {batch_num} failed after {max_retries} attempts: {e}")

        # Return error entries for each artifact in the batch
        return [
            ArtifactAnalysis(
                artifact_path=path,
                key_findings=f"Analysis failed: {str(last_error)}",
            )
            for path in batch.keys()
        ]

    def _analyze_all_artifacts(self, artifacts: dict[str, str] | None) -> list[ArtifactAnalysis]:
        """Analyze all diagnostic artifacts in batches."""
        if not artifacts:
            return []

        # Split into batches respecting token limits (use 80% of budget for safety)
        safe_batch_tokens = int(self.tokens_per_artifact_batch * 0.8)
        batches = self._batch_artifacts_by_tokens(artifacts, safe_batch_tokens)

        logger.info(f"Analyzing {len(artifacts)} artifacts in {len(batches)} batch(es)")

        all_analyses = []
        for i, batch in enumerate(batches, 1):
            batch_analyses = self._analyze_artifact_batch(batch, i)
            all_analyses.extend(batch_analyses)

        return all_analyses

    def _build_artifacts_context(self, artifact_analyses: list[ArtifactAnalysis]) -> dict[str, Any]:
        """Build artifacts context from analyses for RCA generation."""
        if not artifact_analyses:
            return {}

        return {
            "note": "Supplemental diagnostic artifacts providing system/cluster context.",
            "analyses": [
                {
                    "artifact_path": a.artifact_path,
                    "key_findings": a.key_findings,
                }
                for a in artifact_analyses
            ],
        }

    def _create_synthesis_context(
        self,
        step_analyses: list[StepAnalysis],
        test_analyses: list[TestFailureAnalysis],
        artifact_analyses: list[ArtifactAnalysis],
    ) -> tuple[str, str, str]:
        """Create unified context for RCA generation."""
        steps_dict = [
            {
                "step_name": a.step_name,
                "failure_category": a.failure_category,
                "root_cause": a.root_cause,
                "evidence": a.evidence,  # Already a list of dicts
            }
            for a in step_analyses
        ]

        tests_dict = [
            {
                "test_identifier": a.test_identifier,
                "source_file": a.source_file,
                "root_cause_summary": a.root_cause_summary,
            }
            for a in test_analyses
        ]

        artifacts_dict = self._build_artifacts_context(artifact_analyses)

        artifact_count = len(artifacts_dict.get("analyses", []))
        logger.info(f"Synthesis: {len(steps_dict)} steps, {len(tests_dict)} tests, {artifact_count} artifacts")

        return json.dumps(steps_dict, indent=2), json.dumps(tests_dict, indent=2), json.dumps(artifacts_dict, indent=2)

    def _create_error_report(
        self,
        job_result: JobResult,
        error: Exception,
        step_analyses: list[StepAnalysis],
        test_analyses: list[TestFailureAnalysis],
        artifact_analyses: list[ArtifactAnalysis],
    ) -> RCAReport:
        """Create report when RCA generation fails."""
        return RCAReport(
            job_name=job_result.job_name,
            build_id=job_result.build_id,
            pr_number=job_result.pr_number,
            summary=f"RCA generation failed: {str(error)}",
            detailed_analysis="Unable to generate detailed analysis.",
            category="unknown",
            step_analyses=step_analyses,
            test_analyses=test_analyses,
            artifact_analyses=artifact_analyses,
        )

    def forward(self, job_result: JobResult) -> RCAReport:
        """Analyze job failures and generate RCA report.

        Raises:
            ValueError: If there are no failures to analyze
        """
        logger.info(f"Starting analysis of {len(job_result.failed_steps)} failed steps")

        if not job_result.failed_steps:
            raise ValueError("No failures to analyze - job passed or no failed steps found")

        step_analyses = [self._analyze_step(step, job_result.step_graph) for step in job_result.failed_steps]
        test_analyses = self._analyze_all_test_failures(job_result.failed_tests)
        artifact_analyses = self._analyze_all_artifacts(job_result.additional_artifacts)

        steps_json, tests_json, artifacts_json = self._create_synthesis_context(
            step_analyses, test_analyses, artifact_analyses
        )

        logger.info("Generating overall RCA")
        try:
            rca = self.rca_generator(
                job_name=job_result.job_name,
                build_id=job_result.build_id,
                pr_number=job_result.pr_number or "N/A",
                failed_steps_analysis=steps_json,
                failed_tests_analysis=tests_json,
                additional_context=artifacts_json,
            )

            return RCAReport(
                job_name=job_result.job_name,
                build_id=job_result.build_id,
                pr_number=job_result.pr_number,
                summary=rca.summary,
                detailed_analysis=rca.detailed_analysis,
                category=rca.category,
                step_analyses=step_analyses,
                test_analyses=test_analyses,
                artifact_analyses=artifact_analyses,
            )
        except Exception as e:
            logger.error(f"RCA generation failed: {e}")
            return self._create_error_report(job_result, e, step_analyses, test_analyses, artifact_analyses)
