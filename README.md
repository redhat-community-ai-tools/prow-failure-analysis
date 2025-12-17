# prow-failure-analysis

Analyzes Prow/OpenShift CI pipeline failures and generates concise root cause reports using large language models. It fetches build artifacts from Google Cloud Storage, intelligently preprocesses logs, and identifies what went wrong and why.

## What It Does

When your OpenShift CI pipeline fails, this tool:

1. **Fetches** build artifacts, logs, and test results from GCS
2. **Parses** xUnit test results and failed step logs
3. **Preprocesses** logs using [cordon](https://github.com/calebevans/cordon) to extract the most relevant failure information
4. **Analyzes** failures (steps, tests, and artifacts) using LLMs to identify root causes
5. **Synthesizes** findings into a concise root cause report
6. **Posts** results as comments on GitHub PRs (optional)

The analysis is context-aware—it understands pipeline structure, test failures, and log patterns to provide meaningful diagnostics rather than just dumping logs.

## Configuration

Configuration is done via environment variables:

### Required Variables

```bash
# Job identification
export JOB_NAME="pull-ci-openshift-kubernetes-master-e2e"
export BUILD_ID="1234567890"

# LLM configuration
export LLM_PROVIDER="gemini"                         # or "anthropic", "ollama", etc.
export LLM_MODEL="gemini-2.5-flash-lite"             # model name
export LLM_API_KEY="your-api-key"                    # API key for the provider
```

### Optional Variables

```bash
# For PR-triggered jobs
export PULL_NUMBER="123"                        # PR number
export ORG_REPO="openshift/kubernetes"          # or "openshift_kubernetes"

# GCS configuration
export GCS_BUCKET="test-platform-results"       # default: test-platform-results
export GCS_CREDS_PATH="/path/to/creds.json"     # for authenticated access

# LLM configuration
export LLM_BASE_URL="https://custom.api.com"    # custom API endpoint

# Preprocessing
export CORDON_DEVICE="cpu"                      # cordon device: "cpu", "cuda", "mps"

# Filtering
export IGNORED_STEPS="gather-*,setup-*"         # glob patterns for steps to ignore
export INCLUDED_ARTIFACTS="gather-must-gather/*,gather-extra/*"  # artifacts to include

# GitHub integration (see docs/github-token-setup.md)
export GITHUB_TOKEN="ghp_..."                   # for posting PR comments and repo validation
```

## Usage

### Basic Analysis

Analyze a failed build and print the report:

```bash
prow-failure-analysis analyze \
  --job-name pull-ci-openshift-kubernetes-master-e2e \
  --build-id 1234567890
```

Or use environment variables:

```bash
export JOB_NAME="pull-ci-openshift-kubernetes-master-e2e"
export BUILD_ID="1234567890"
export LLM_PROVIDER="openai"
export LLM_MODEL="gpt-4o"
export LLM_API_KEY="sk-..."

prow-failure-analysis analyze
```

### PR-Triggered Jobs

For jobs triggered by pull requests:

```bash
prow-failure-analysis analyze \
  --job-name pull-ci-org-repo-main-test \
  --build-id 1234567890 \
  --pr-number 456 \
  --org-repo org/repo
```

The tool will automatically infer the org/repo from the job name when possible. Use `--org-repo` to be safe.

### Post Results to GitHub

Automatically comment on the PR that triggered the job. See [`docs/github-token-setup.md`](docs/github-token-setup.md) for GitHub token setup instructions.

```bash
export GITHUB_TOKEN="ghp_..."

prow-failure-analysis analyze \
  --job-name pull-ci-org-repo-main-test \
  --build-id 1234567890 \
  --pr-number 456 \
  --post-comment
```

## How It Works

### 1. Data Collection

The tool fetches from GCS:
- `finished.json` - job metadata and status
- `build-log.txt` - logs for each failed step
- `junit_*.xml` - test result files
- `ci-operator-step-graph.json` - pipeline structure
- Additional artifacts matching configured patterns

### 2. Intelligent Preprocessing

Large logs are preprocessed using [cordon](https://github.com/calebevans/cordon), which uses semantic embeddings to detect anomalies:
- Identifies semantically unusual log sections (not just error keywords)
- Filters out repetitive normal operations while preserving unique failures
- Reduces log size while keeping the signal

### 3. LLM Analysis

The tool uses [DSPy](https://github.com/stanfordnlp/dspy) to orchestrate LLM calls:

1. **Step Analysis** - Each failed step is analyzed independently to identify:
   - Failure category (e.g., infrastructure, test, configuration)
   - Root cause
   - Supporting evidence

2. **Test Analysis** - Failed tests are analyzed to understand:
   - What the test was checking
   - Why it failed
   - Root cause summary

3. **Artifact Analysis** - Diagnostic artifacts (cluster state, resource dumps, etc.) are analyzed in batches to extract:
   - Key environmental details
   - Relevant anomalies or configuration issues
   - Supplemental context for diagnosis

4. **RCA Synthesis** - All analyses are synthesized into a cohesive report with:
   - High-level summary of the primary blocking failure
   - Detailed technical analysis with contributing factors
   - Overall failure category

### 4. Report Generation

The final report includes:
- **Root Cause** - Concise explanation of what went wrong
- **Technical Details** - In-depth analysis with context
- **Evidence** - Specific log excerpts and test failures supporting the diagnosis

## Features

### Secret Leak Detection

The tool automatically scans all output (GitHub comments, console logs, and reports) for sensitive information before displaying it. This prevents accidental exposure of secrets in public comments or logs.

**What it detects:**
- AWS access keys and secret keys
- GitHub tokens (personal access tokens, OAuth tokens)
- Private keys (RSA, SSH, etc.)
- JWT tokens
- API keys from various services (Slack, Stripe, Twilio, etc.)
- High-entropy strings (Base64, Hex) that may be secrets
- Database connection strings with credentials
- Basic authentication credentials

**How it works:**
- Uses the [detect-secrets](https://github.com/Yelp/detect-secrets) library to scan text
- Automatically redacts detected secrets with labeled placeholders: `[REDACTED: AWS Access Key]`
- Applied at multiple layers for defense in depth:
  - During report generation
  - Before posting GitHub comments
  - Before printing to console

### Dynamic Token Budgeting

The tool automatically adjusts token allocation based on:
- Number of failed steps and tests
- LLM context window size
- Relative importance of different failure types

This ensures optimal use of the model's context window without truncating critical information.

### Step Filtering

Ignore noisy or irrelevant steps using glob patterns:

```bash
export IGNORED_STEPS="gather-*,teardown-*,ipi-*"
```

This is useful for:
- Focusing analysis on relevant failures
- Excluding known flaky steps
- Reducing processing time and token usage

### Artifact Inclusion

Include additional diagnostic files in the analysis:

```bash
export INCLUDED_ARTIFACTS="gather-must-gather/*,gather-extra/artifacts/pods/*/*"
```

Supports directory patterns with `/*` syntax. Binary files are automatically excluded.

**Batched Analysis**: Artifacts are analyzed in batches to minimize API overhead while respecting token limits. Multiple small artifacts are processed together in a single LLM call for efficiency.

### Multiple LLM Providers

Works with any LLM provider supported by DSPy/LiteLLM:

```bash
# Google Gemini
export LLM_PROVIDER="gemini"
export LLM_MODEL="gemini-2.5-flash"

# OpenAI
export LLM_PROVIDER="openai"
export LLM_MODEL="gpt-4o"

# Anthropic
export LLM_PROVIDER="anthropic"
export LLM_MODEL="claude-3-5-sonnet-20241022"

# Local Ollama
export LLM_PROVIDER="ollama"
export LLM_MODEL="llama3.1:70b"
# API key not required for Ollama

# Custom endpoint
export LLM_PROVIDER="openai"
export LLM_BASE_URL="https://custom-llm-gateway.example.com"
```

## Example Output

[Example from failed job #2000504097274335232](https://prow.ci.openshift.org/view/gs/test-platform-results/pr-logs/pull/redhat-appstudio_infra-deployments/9663/pull-ci-redhat-appstudio-infra-deployments-main-appstudio-e2e-tests/2000504097274335232)

```
================================================================================
# Pipeline Failure Analysis
**Job:** `pull-ci-redhat-appstudio-infra-deployments-main-appstudio-e2e-tests`
**Build:** `2000504097274335232` | **PR:** #9663 | **Category:** Build

---
## Root Cause

Pipeline failed to build due to an inability to pull a required Helm chart for the squid component.

## Technical Details

- **Immediate Cause:** The `kustomize build` command failed during the `appstudio-e2e-tests/redhat-appstudio-e2e` step. This was directly caused by an inability to pull the Helm chart `quay.io/konflux-ci/caching/squid-helm` at version `0.1.802_09c1de7` from its OCI repository.
- **Contributing Factors:** The specific Helm chart version required by the `squid-in-cluster-local` component was not found in the specified repository. This indicates a potential issue with chart availability, versioning, or the repository configuration itself.
- **Impact:** The failure to pull the Helm chart prevented the `kustomize build` process from generating the necessary Kubernetes manifests. This directly blocked the execution of the e2e tests and caused the overall pipeline job to fail.

## Evidence

**appstudio-e2e-tests/redhat-appstudio-e2e** — *build*

- squid-in-cluster-local failed with:
- [{"lastTransitionTime":"2025-12-15T10:23:20Z","message":"Failed to load target state: failed to generate manifest for source 1 of 1: rpc error: code = Unknown desc = `kustomize build <path to cached source>/components/squid/development --enable-helm ... (is 'helm' installed?): exit status 1","type":"ComparisonError"}]
- Error: Error: quay.io/konflux-ci/caching/squid-helm:0.1.802_09c1de7: not found
- : unable to run: 'helm pull --untar --untardir <path to cached source>/components/squid/development/charts/squid-helm-0.1.802+09c1de7 oci://quay.io/konflux-ci/caching/squid-helm --version 0.1.802+09c1de7' with env=[HELM_CONFIG_HOME=/tmp/kustomize-helm-2467139826/helm HELM_CACHE_HOME=/tmp/kustomize-helm-2467139826/helm/.cache HELM_DATA_HOME=/tmp/kustomize-helm-2467139826/helm/.data] (is 'helm' installed?): exit status 1
- Error: error when bootstrapping cluster: reached maximum number of attempts (2). error: exit status 1


================================================================================
```
