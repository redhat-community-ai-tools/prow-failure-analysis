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
    evidence: str = dspy.OutputField(
        desc=(
            'JSON array of evidence items. Each item has "source" (artifact path) and "content" (log excerpt). '
            'Format: [{"source": "artifacts/step-name/build-log.txt line 123", "content": "error message here"}, ...]. '
            "Use actual artifact paths relative to the job (e.g., artifacts/step-name/build-log.txt). "
            "Include line numbers when relevant. Content should be verbatim log/error text. "
            "Return valid JSON array - escape quotes and newlines properly."
        )
    )


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
    7. NEVER fabricate specific details like IP addresses, port numbers, error codes, file paths,
       database names, service names, or metrics that do not appear in the provided analyses.
       Only cite details that are explicitly present in the input data.

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
            "Structured technical explanation. Format as:\n"
            "### Immediate Cause\n"
            "(what directly failed - 1-2 sentences)\n\n"
            "### Contributing Factors\n"
            "(related issues if any - 1-2 sentences)\n\n"
            "### Impact\n"
            "(how this blocked the pipeline - 1-2 sentences)\n\n"
            "Use markdown subheadings (###) for each section. Be concise and scannable."
        )
    )
    category: str = dspy.OutputField(
        desc="Primary failure category: infrastructure/test/build/configuration/timeout/unknown"
    )
    contributing_artifact_paths: str = dspy.OutputField(
        desc=(
            "JSON array of up to 10 artifact paths from additional_context that are most relevant "
            "to the failure as contributing factors. Select only artifacts whose findings are directly "
            "related to the root cause or environmental issues that likely contributed to the failure. "
            "Return exact artifact_path strings from the input. "
            'Example: ["pods/controller.log", "events.json"]. '
            "Return an empty array [] if no artifacts are relevant."
        )
    )
