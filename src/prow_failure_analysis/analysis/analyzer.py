import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import dspy
from genji import LLMBackend as GenjiBackend
from genji import Template as GenjiTemplate

from ..gcs.models import JobResult, StepResult
from ..parsing.xunit_models import FailedTest
from ..security.leak_detector import LeakDetector
from ..utils import retry_with_backoff
from .signatures import AnalyzeStepFailure, AnalyzeTestFailure, GenerateRCA

logger = logging.getLogger(__name__)


def _estimate_tokens(text: str) -> int:
    """Rough token estimation (chars / 4)."""
    return len(text) // 4


def _sanitize_json_string(text: str) -> str:
    """Sanitize JSON string by escaping unescaped control characters.

    LLMs often return JSON with literal newlines/tabs inside strings instead of
    properly escaped \\n and \\t sequences. This causes json.loads() to fail with
    "Invalid control character" errors.
    """
    import re

    # Find all string values in the JSON and escape control characters within them
    # This regex matches JSON string values (content between quotes, handling escapes)
    def escape_control_chars(match: re.Match[str]) -> str:
        content = match.group(1)
        content = content.replace("\n", "\\n")
        content = content.replace("\r", "\\r")
        content = content.replace("\t", "\\t")
        content = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", content)
        return f'"{content}"'

    return re.sub(r'"((?:[^"\\]|\\.)*)(?<!\\)"', escape_control_chars, text)


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
    contributing_artifact_paths: list[str] = field(default_factory=list)

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

                        # Use expandable details - only show source in summary
                        parts.append(f"<details>\n<summary><code>{source}</code></summary>\n\n")
                        parts.append(f"```\n{content}\n```\n</details>\n\n")

        # Add LLM-ranked contributing factors from artifact analyses (within Evidence section)
        if self.contributing_artifact_paths:
            artifact_lookup = {a.artifact_path: a for a in self.artifact_analyses}
            contributing = [
                artifact_lookup[path]
                for path in self.contributing_artifact_paths
                if path in artifact_lookup
                and artifact_lookup[path].key_findings
                and "no significant findings" not in artifact_lookup[path].key_findings.lower()
                and "analysis failed" not in artifact_lookup[path].key_findings.lower()
            ]
            if contributing:
                if not self.step_analyses:
                    parts.append("## Evidence\n\n")
                parts.append("### Contributing Factors\n\n")
                for artifact in contributing:
                    findings = artifact.key_findings.replace("`", "'").strip()
                    parts.append(f"<details>\n<summary><code>{artifact.artifact_path}</code></summary>\n\n")
                    parts.append(f"{findings}\n</details>\n\n")

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
        self.rca_generator = dspy.ChainOfThought(GenerateRCA)
        self.preprocessor = preprocessor
        self.config = config
        self.tokens_per_step = tokens_per_step
        self.tokens_per_test = tokens_per_test
        self.tokens_per_artifact_batch = tokens_per_artifact_batch

        # Initialize Genji backend for artifact analysis (guarantees valid JSON output)
        self._genji_backend: GenjiBackend | None = None
        if config:
            model_str = f"{config.llm_provider}/{config.llm_model}"
            backend_kwargs: dict[str, Any] = {"model": model_str, "temperature": 0.0}
            if config.llm_api_key:
                backend_kwargs["api_key"] = config.llm_api_key
            if config.llm_base_url:
                backend_kwargs["base_url"] = config.llm_base_url
            self._genji_backend = GenjiBackend(**backend_kwargs)

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

    @retry_with_backoff(max_retries=3, base_delay=2.0, rate_limit_delay=10.0)
    def _call_step_analyzer(self, step_name: str, log_content: str, step_context: str) -> Any:
        """Call DSPy step analyzer with retry handling."""
        return self.step_analyzer(
            step_name=step_name,
            log_content=log_content,
            step_context=step_context,
        )

    def _analyze_step(self, step: StepResult, step_graph: dict[str, Any]) -> StepAnalysis:
        """Analyze a single failed step with automatic retry logic."""
        logger.info(f"Analyzing step: {step.name}")

        step_context = self._get_step_context(step, step_graph)
        log_content = self._read_log_content(step)

        try:
            result = self._call_step_analyzer(step.name, log_content, step_context)

            # Parse evidence JSON
            evidence_list = []
            try:
                raw_evidence = result.evidence or "[]"
                # Sanitize control characters that LLMs often include unescaped
                sanitized_evidence = _sanitize_json_string(raw_evidence)
                evidence_list = json.loads(sanitized_evidence)
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
        except Exception as e:
            logger.error(f"Step {step.name}: analysis failed after all retries: {e}")
            return StepAnalysis(
                step_name=step.name,
                failure_category="unknown",
                root_cause=f"Analysis failed: {str(e)}",
                evidence=[],
            )

    @retry_with_backoff(max_retries=3, base_delay=2.0, rate_limit_delay=10.0)
    def _call_test_analyzer(
        self, test_identifier: str, failure_type: str, failure_message: str, failure_details: str
    ) -> Any:
        """Call DSPy test analyzer with retry handling."""
        return self.test_analyzer(
            test_identifier=test_identifier,
            failure_type=failure_type,
            failure_message=failure_message,
            failure_details=failure_details,
        )

    def _analyze_test_failure(self, test: FailedTest) -> TestFailureAnalysis:
        """Analyze a single test failure with automatic retry logic."""
        logger.info(f"Analyzing test: {test.test_identifier}")

        details = test.combined_details
        if self.preprocessor:
            details = self.preprocessor.preprocess(
                details, f"test:{test.test_identifier}", max_tokens=self.tokens_per_test
            )

        try:
            result = self._call_test_analyzer(
                test.test_identifier,
                test.failure_type or test.error_type or "Unknown",
                test.failure_message or test.error_message or "No message",
                details,
            )

            return TestFailureAnalysis(
                test_identifier=test.test_identifier,
                source_file=test.source_file,
                root_cause_summary=result.root_cause_summary,
            )
        except Exception as e:
            logger.error(f"Test {test.test_identifier}: analysis failed after all retries: {e}")
            return TestFailureAnalysis(
                test_identifier=test.test_identifier,
                source_file=test.source_file,
                root_cause_summary=f"Analysis failed: {str(e)}",
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
        current_tokens = 500  # Conservative JSON overhead estimate

        # Use only 60% of max_tokens to leave room for the prompt and output
        safe_max_tokens = int(max_tokens * 0.60)

        for path, content in artifacts.items():
            # Be more conservative with token estimation (multiply by 1.2 safety factor)
            artifact_tokens = int((_estimate_tokens(path) + _estimate_tokens(content) + 100) * 1.2)

            # If a single artifact is too large, split it into its own batch
            if artifact_tokens > safe_max_tokens:
                # If there's a current batch, save it first
                if current_batch:
                    batches.append(current_batch)
                    current_batch = {}
                    current_tokens = 500

                # Put the large artifact in its own batch (will be caught by safety check)
                batches.append({path: content})
                logger.warning(
                    f"Artifact {path} is very large ({artifact_tokens:,} tokens), " "placing in separate batch"
                )
                continue

            # If adding this artifact would exceed limit, start new batch
            if current_tokens + artifact_tokens > safe_max_tokens and current_batch:
                batches.append(current_batch)
                current_batch = {}
                current_tokens = 500

            current_batch[path] = content
            current_tokens += artifact_tokens

        if current_batch:
            batches.append(current_batch)

        return batches

    def _analyze_artifact_batch(self, batch: dict[str, str], batch_num: int) -> list[ArtifactAnalysis]:
        """Analyze a batch of artifacts using Genji template rendering.

        Genji templates own the JSON structure (brackets, commas, keys) while the LLM
        only generates string content via gen() calls. This guarantees valid JSON output
        and eliminates the parsing failures seen with the previous DSPy approach.
        """
        if not batch:
            return []

        logger.info(f"Analyzing artifact batch {batch_num} ({len(batch)} artifacts)")

        # Check if the batch itself is too large (safety check)
        batch_tokens = _estimate_tokens(json.dumps(batch))
        # Use 90% of context limit as absolute max for a single batch
        if self.config:
            max_batch_tokens = int(self.config.detect_model_context_limit() * 0.90)
            if batch_tokens > max_batch_tokens:
                logger.error(
                    f"Artifact batch {batch_num} exceeds safe token limit "
                    f"({batch_tokens:,} > {max_batch_tokens:,} tokens). Skipping analysis."
                )
                return [
                    ArtifactAnalysis(
                        artifact_path=path,
                        key_findings="Artifact too large for analysis (exceeds context window)",
                    )
                    for path in batch.keys()
                ]

        try:
            template = GenjiTemplate.from_file(
                Path(__file__).parent / "templates" / "artifact_analysis.json.genji",
                backend=self._genji_backend,
            )
            rendered = template.render(artifacts=batch)
            findings_list = json.loads(rendered)

            return [
                ArtifactAnalysis(
                    artifact_path=finding["artifact_path"],
                    key_findings=finding["key_findings"],
                )
                for finding in findings_list
            ]
        except Exception as e:
            logger.error(f"Artifact batch {batch_num}: analysis failed: {e}")
            return [
                ArtifactAnalysis(
                    artifact_path=path,
                    key_findings=f"Analysis failed: {str(e)}",
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

    @retry_with_backoff(max_retries=3, base_delay=2.0, rate_limit_delay=10.0)
    def _call_rca_generator(
        self,
        job_name: str,
        build_id: str,
        pr_number: str,
        failed_steps_analysis: str,
        failed_tests_analysis: str,
        additional_context: str,
    ) -> Any:
        """Call DSPy RCA generator with retry handling."""
        return self.rca_generator(
            job_name=job_name,
            build_id=build_id,
            pr_number=pr_number,
            failed_steps_analysis=failed_steps_analysis,
            failed_tests_analysis=failed_tests_analysis,
            additional_context=additional_context,
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
            rca = self._call_rca_generator(
                job_result.job_name,
                job_result.build_id,
                job_result.pr_number or "N/A",
                steps_json,
                tests_json,
                artifacts_json,
            )

            # Parse LLM-ranked contributing artifact paths
            contributing_paths: list[str] = []
            try:
                raw_paths = getattr(rca, "contributing_artifact_paths", "[]")
                sanitized = _sanitize_json_string(raw_paths)
                parsed = json.loads(sanitized)
                if isinstance(parsed, list):
                    contributing_paths = [str(p) for p in parsed]
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"Failed to parse contributing_artifact_paths: {e}")

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
                contributing_artifact_paths=contributing_paths,
            )
        except Exception as e:
            logger.error(f"RCA generation failed after all retries: {e}")
            return self._create_error_report(job_result, e, step_analyses, test_analyses, artifact_analyses)
