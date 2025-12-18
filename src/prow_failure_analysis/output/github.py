import logging
from typing import TYPE_CHECKING

from github import Auth, Github

from ..security.leak_detector import LeakDetector

if TYPE_CHECKING:
    from ..analysis.analyzer import RCAReport

logger = logging.getLogger(__name__)


def post_pr_comment(
    github_token: str,
    org_repo: str,
    pr_number: int,
    report: "RCAReport",
) -> None:
    """Post RCA report as a comment on a GitHub PR.

    Args:
        github_token: GitHub personal access token
        org_repo: Repository in format "org/repo" or "org_repo"
        pr_number: PR number
        report: RCA report object

    Raises:
        Exception: If posting comment fails
    """
    github_repo = org_repo.replace("_", "/")
    logger.info(f"Posting comment to {github_repo}#{pr_number}")

    auth = Auth.Token(github_token)
    g = Github(auth=auth)

    try:
        repo = g.get_repo(github_repo)
        pr = repo.get_pull(pr_number)

        comment_body = f"""## 🤖 Pipeline Failure Analysis

**Category:** {report.category.title()}

{report.summary}

### 📋 Technical Details

{report.detailed_analysis}
"""

        if report.step_analyses:
            comment_body += """
<details>
<summary><b>🔍 Evidence</b></summary>

"""
            for analysis in report.step_analyses:
                comment_body += f"### {analysis.step_name}\n\n"
                comment_body += f"**Category:** `{analysis.failure_category}`  \n"
                comment_body += f"**Root Cause:** {analysis.root_cause}\n\n"
                if analysis.evidence:
                    comment_body += "**Logs:**\n\n"
                    for item in analysis.evidence:
                        source = item.get("source", "unknown")
                        content = item.get("content", "").replace("`", "'")
                        # Use details/summary for expandable evidence - only show source in summary
                        comment_body += (
                            f"<details>\n<summary><code>{source}</code></summary>\n\n"
                            f"```\n{content}\n```\n</details>\n\n"
                        )
                comment_body += "\n"

            comment_body += "</details>\n"

        repo_url = "https://github.com/redhat-community-ai-tools/prow-failure-analysis"
        comment_body += f"""
---
*Analysis powered by [prow-failure-analysis]({repo_url}) | Build: `{report.build_id}`*
"""

        # Final safety check: sanitize comment body to prevent any secret leaks
        leak_detector = LeakDetector()
        sanitized_comment = leak_detector.sanitize_text(comment_body)

        pr.create_issue_comment(sanitized_comment)
        logger.info("Comment posted successfully")

    finally:
        g.close()
