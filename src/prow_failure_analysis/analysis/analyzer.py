import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import dspy

from ..gcs.models import JobResult, StepResult
from ..parsing.xunit_models import FailedTest
from .signatures import AnalyzeStepFailure, AnalyzeTestFailure, GenerateRCA

logger = logging.getLogger(__name__)


@dataclass
class StepAnalysis:
    """Analysis result for a single step."""

    step_name: str
    failure_category: str
    root_cause: str
    evidence: list[str]


@dataclass
class TestFailureAnalysis:
    """Analysis result for a single test failure."""

    test_identifier: str
    source_file: str
    root_cause_summary: str


@dataclass
class RCAReport:
    """Complete root cause analysis report."""

    job_name: str
    build_id: str
    pr_number: str | None
    summary: str
    detailed_analysis: str
    is_infrastructure: bool
    step_analyses: list[StepAnalysis]
    test_analyses: list[TestFailureAnalysis] = field(default_factory=list)

    def to_markdown(self) -> str:
        """Generate markdown formatted report."""
        parts = [
            "# Pipeline Failure Analysis\n",
            f"**Job:** `{self.job_name}`\n",
            f"**Build:** `{self.build_id}`",
        ]

        if self.pr_number:
            parts.append(f" | **PR:** #{self.pr_number}")
        if self.is_infrastructure:
            parts.append(" | **Infrastructure Issue** ⚠️")

        parts.extend(["\n\n---\n", "## Root Cause\n\n", f"{self.summary}\n\n"])
        parts.extend(["## Technical Details\n\n", f"{self.detailed_analysis}\n\n"])

        if self.step_analyses:
            parts.append("## Evidence\n\n")
            for analysis in self.step_analyses:
                parts.append(f"**{analysis.step_name}** — *{analysis.failure_category}*\n\n")
                if analysis.evidence:
                    parts.extend([f"- {evidence}\n" for evidence in analysis.evidence])
                    parts.append("\n")

        return "".join(parts)


class FailureAnalyzer(dspy.Module):
    """DSPy module for analyzing pipeline failures."""

    def __init__(
        self,
        preprocessor: Any = None,
        config: Any = None,
        tokens_per_step: int = 100_000,
        tokens_per_test: int = 50_000,
    ) -> None:
        """Initialize the analyzer.

        Args:
            preprocessor: LogPreprocessor for reducing test details
            config: Config for token limits
            tokens_per_step: Token limit per step
            tokens_per_test: Token limit per test
        """
        super().__init__()
        self.step_analyzer = dspy.ChainOfThought(AnalyzeStepFailure)
        self.test_analyzer = dspy.ChainOfThought(AnalyzeTestFailure)
        self.rca_generator = dspy.ChainOfThought(GenerateRCA)
        self.preprocessor = preprocessor
        self.config = config
        self.tokens_per_step = tokens_per_step
        self.tokens_per_test = tokens_per_test

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

    def _analyze_step(self, step: StepResult, step_graph: dict[str, Any]) -> StepAnalysis:
        """Analyze a single failed step."""
        logger.info(f"Analyzing step: {step.name}")

        step_context = self._get_step_context(step, step_graph)
        log_content = self._read_log_content(step)

        try:
            result = self.step_analyzer(
                step_name=step.name,
                log_content=log_content,
                step_context=step_context,
            )

            return StepAnalysis(
                step_name=step.name,
                failure_category=result.failure_category,
                root_cause=result.root_cause,
                evidence=result.evidence if isinstance(result.evidence, list) else [],
            )
        except Exception as e:
            logger.error(f"Step {step.name}: analysis failed: {e}")
            return StepAnalysis(
                step_name=step.name,
                failure_category="unknown",
                root_cause=f"Analysis failed: {str(e)}",
                evidence=[],
            )

    def _analyze_test_failure(self, test: FailedTest) -> TestFailureAnalysis:
        """Analyze a single test failure."""
        logger.info(f"Analyzing test: {test.test_identifier}")

        details = test.combined_details
        if self.preprocessor:
            details = self.preprocessor.preprocess(
                details, f"test:{test.test_identifier}", max_tokens=self.tokens_per_test
            )

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
        except Exception as e:
            logger.error(f"Test {test.test_identifier}: analysis failed: {e}")
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

    def _build_artifacts_context(self, additional_artifacts: dict[str, str] | None) -> dict[str, Any]:
        """Build artifacts context for RCA generation."""
        if not additional_artifacts:
            return {}

        return {
            "note": "Supplemental diagnostic artifacts for context only, not primary failure sources.",
            "files": {
                path: {
                    "path": path,
                    "content_preview": content[:1000] if len(content) > 1000 else content,
                    "size": len(content),
                }
                for path, content in additional_artifacts.items()
            },
        }

    def _create_synthesis_context(
        self,
        step_analyses: list[StepAnalysis],
        test_analyses: list[TestFailureAnalysis],
        additional_artifacts: dict[str, str] | None = None,
    ) -> tuple[str, str, str]:
        """Create unified context for RCA generation."""
        steps_dict = [
            {
                "step_name": a.step_name,
                "failure_category": a.failure_category,
                "root_cause": a.root_cause,
                "evidence": a.evidence,
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

        artifacts_dict = self._build_artifacts_context(additional_artifacts)

        artifact_count = len(artifacts_dict.get("files", {}))
        logger.info(f"Synthesis: {len(steps_dict)} steps, {len(tests_dict)} tests, {artifact_count} artifacts")

        return json.dumps(steps_dict, indent=2), json.dumps(tests_dict, indent=2), json.dumps(artifacts_dict, indent=2)

    def _create_empty_report(self, job_result: JobResult) -> RCAReport:
        """Create report for jobs with no failures."""
        return RCAReport(
            job_name=job_result.job_name,
            build_id=job_result.build_id,
            pr_number=job_result.pr_number,
            summary="No failures detected in this job run.",
            detailed_analysis="All steps completed successfully.",
            is_infrastructure=False,
            step_analyses=[],
        )

    def _create_error_report(
        self,
        job_result: JobResult,
        error: Exception,
        step_analyses: list[StepAnalysis],
        test_analyses: list[TestFailureAnalysis],
    ) -> RCAReport:
        """Create report when RCA generation fails."""
        return RCAReport(
            job_name=job_result.job_name,
            build_id=job_result.build_id,
            pr_number=job_result.pr_number,
            summary=f"RCA generation failed: {str(error)}",
            detailed_analysis="Unable to generate detailed analysis.",
            is_infrastructure=False,
            step_analyses=step_analyses,
            test_analyses=test_analyses,
        )

    def forward(self, job_result: JobResult) -> RCAReport:
        """Analyze job failures and generate RCA report."""
        logger.info(f"Starting analysis of {len(job_result.failed_steps)} failed steps")

        if not job_result.failed_steps:
            logger.warning("No failed steps to analyze")
            return self._create_empty_report(job_result)

        step_analyses = [self._analyze_step(step, job_result.step_graph) for step in job_result.failed_steps]
        test_analyses = self._analyze_all_test_failures(job_result.failed_tests)

        steps_json, tests_json, artifacts_json = self._create_synthesis_context(
            step_analyses, test_analyses, job_result.additional_artifacts
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
                is_infrastructure=rca.is_infrastructure,
                step_analyses=step_analyses,
                test_analyses=test_analyses,
            )
        except Exception as e:
            logger.error(f"RCA generation failed: {e}")
            return self._create_error_report(job_result, e, step_analyses, test_analyses)
