import dspy


class AnalyzeStepFailure(dspy.Signature):
    """Analyze a CI pipeline step failure and identify the root cause.

    IMPORTANT: Logs have been preprocessed with semantic anomaly detection.
    - Only the most anomalous/unusual log sections are shown
    - Normal/repeated patterns are removed to reduce noise
    - Log sections are wrapped in XML tags: <block lines="X-Y" score="S">...</block>
    - Score indicates anomaly level (higher = more unusual, typically 0.0-1.0)
    - Higher scored blocks often contain errors, exceptions, or critical events
    - Final output lines are always included

    Focus on high-scoring blocks and error patterns. Be concise and specific.
    """

    step_name: str = dspy.InputField(desc="Name of the failed step")
    log_content: str = dspy.InputField(desc="Semantically filtered log content showing anomalous/error lines")
    step_context: str = dspy.InputField(desc="Step's position and dependencies in the pipeline")

    failure_category: str = dspy.OutputField(
        desc="Failure category: infrastructure/test/build/configuration/timeout/unknown"
    )
    root_cause: str = dspy.OutputField(desc="Concise technical root cause (1-2 sentences)")
    evidence: list[str] = dspy.OutputField(desc="3-5 key log excerpts showing the failure. Be selective.")


class AnalyzeTestFailure(dspy.Signature):
    """Analyze a test failure from XUnit results.

    IMPORTANT: Failure details have been preprocessed with semantic anomaly detection.
    - Only anomalous/unusual content is shown
    - Content may be wrapped in XML tags: <block lines="X-Y" score="S">...</block>
    - Higher scores indicate more unusual/critical content
    - Focus on high-scoring blocks for root cause

    Be concise and technical. Identify the immediate failure, not symptoms.
    Distinguish: creation failures vs validation failures vs timeouts.
    """

    test_identifier: str = dspy.InputField(desc="Full test identifier")
    failure_type: str = dspy.InputField(desc="Type of failure or error")
    failure_message: str = dspy.InputField(desc="Failure or error message")
    failure_details: str = dspy.InputField(
        desc="Semantically filtered failure content showing anomalous lines, stack traces, and errors"
    )

    root_cause_summary: str = dspy.OutputField(desc="One sentence stating the immediate technical cause")


class AnalyzeArtifacts(dspy.Signature):
    """Analyze multiple diagnostic artifacts to extract key findings from each.

    Artifacts provide supplemental context about cluster/system state.
    They are NOT failure sources - extract relevant environmental details.

    IMPORTANT: Content has been preprocessed with semantic anomaly detection.
    - Only anomalous/unusual sections are shown
    - May be wrapped in XML tags: <block lines="X-Y" score="S">...</block>

    Process each artifact independently and return findings for each.
    """

    artifacts_json: str = dspy.InputField(desc="JSON string of dict mapping artifact paths to preprocessed content")

    artifact_findings: str = dspy.OutputField(
        desc=(
            "JSON list of {artifact_path: str, key_findings: str} for each artifact. "
            "key_findings should be 2-3 sentences summarizing relevant details or anomalies."
        )
    )


class GenerateRCA(dspy.Signature):
    """Generate a professional, concise root cause analysis for pipeline failures.

    CRITICAL INSTRUCTIONS:
    1. Identify the PRIMARY blocking failure (what failed FIRST and prevented other operations)
    2. Distinguish PRIMARY (prevented execution) vs SECONDARY (quality/validation checks)
    3. Be concise - avoid repeating the same information in multiple sections
    4. Each section should provide DISTINCT information:
       - Summary: State the PRIMARY root cause in 1-2 sentences
       - Detailed Analysis: Explain WHY it failed and impact (technical details, timeline)
       - Do NOT restate the root cause multiple times
    5. Use professional technical language
    6. Focus on facts from the analyses - do not invent information

    Cross-reference step and test analyses to understand causation.
    """

    job_name: str = dspy.InputField(desc="Name of the Prow job")
    build_id: str = dspy.InputField(desc="Build ID")
    pr_number: str = dspy.InputField(desc="PR number if PR-triggered, otherwise 'N/A'")
    failed_steps_analysis: str = dspy.InputField(desc="JSON string of step failure analyses")
    failed_tests_analysis: str = dspy.InputField(desc="JSON string of test failure analyses")
    additional_context: str = dspy.InputField(
        desc=(
            "JSON string of supplemental diagnostic artifacts (cluster state, resource dumps, etc.). "
            "These are NOT failures - use only for environment context when diagnosing failures above."
        )
    )

    summary: str = dspy.OutputField(
        desc=(
            "Single concise sentence stating WHAT failed (the PRIMARY blocking failure). "
            "Example: 'Pipeline failed to create PipelineRun resource for component X.'"
        )
    )
    detailed_analysis: str = dspy.OutputField(
        desc=(
            "Structured technical explanation using bullet points. Format as:\n"
            "- **Immediate Cause:** (what directly failed)\n"
            "- **Contributing Factors:** (related issues if any)\n"
            "- **Impact:** (how this blocked the pipeline)\n"
            "Keep each bullet to 1-2 sentences. Be scannable for GitHub comments."
        )
    )
    category: str = dspy.OutputField(
        desc="Primary failure category: infrastructure/test/build/configuration/timeout/unknown"
    )
