import logging
from typing import TYPE_CHECKING

from github import Auth, Github

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

<details>
<summary><b>📋 Detailed Analysis</b></summary>

{report.detailed_analysis}

</details>
"""

        if report.step_analyses:
            comment_body += """
<details>
<summary><b>🔍 Failed Steps</b></summary>

"""
            for analysis in report.step_analyses:
                comment_body += f"### {analysis.step_name}\n\n"
                comment_body += f"**Category:** `{analysis.failure_category}`  \n"
                comment_body += f"**Root Cause:** {analysis.root_cause}\n\n"
                if analysis.evidence:
                    comment_body += "**Evidence:**\n"
                    for evidence in analysis.evidence[:3]:
                        comment_body += f"- `{evidence}`\n"
                comment_body += "\n"

            comment_body += "</details>\n"

        repo_url = "https://github.com/redhat-community-ai-tools/prow-failure-analysis"
        comment_body += f"""
---
*Analysis powered by [prow-failure-analysis]({repo_url}) | Build: `{report.build_id}`*
"""

        pr.create_issue_comment(comment_body)
        logger.info("Comment posted successfully")

    finally:
        g.close()
