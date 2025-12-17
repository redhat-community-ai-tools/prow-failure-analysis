# GitHub Token Setup

This guide explains how to create and configure a GitHub Personal Access Token (PAT) for use with prow-failure-analysis.

## Why You Need a GitHub Token

The GitHub token is used for two optional features:

1. **Posting PR Comments** - When using `--post-comment`, the tool posts RCA reports as comments on the pull request that triggered the job
2. **Repository Validation** - Automatically validates org/repo names extracted from job names against GitHub to ensure accuracy

If you don't need these features, you can skip setting up a token.

## Creating a Personal Access Token

### 1. Navigate to GitHub Settings

Go to [https://github.com/settings/tokens](https://github.com/settings/tokens) or:
- Click your profile picture (top-right)
- Settings → Developer settings → Personal access tokens → Tokens (classic)

### 2. Generate New Token

Click **"Generate new token"** → **"Generate new token (classic)"**

### 3. Configure Token

**Name**: `prow-failure-analysis` (or any descriptive name)

**Expiration**: Choose based on your security requirements
- 90 days (recommended for personal use)
- No expiration (for automated systems with secure storage)

**Scopes**: Select the following permissions:

#### Required Scopes

- ✅ **`repo`** - Full control of private repositories
  - Needed to post comments on PRs in private repos
  - Includes `repo:status`, `repo_deployment`, `public_repo`, etc.

If you only work with public repositories, you can use:
- ✅ **`public_repo`** - Access public repositories
- ✅ **`read:org`** - Read org and team membership (for validation)

#### Why These Scopes?

- **PR Comments**: Requires write access to issues (included in `repo` or `public_repo`)
- **Repo Validation**: Requires read access to check if repositories exist

### 4. Generate and Copy Token

1. Click **"Generate token"** at the bottom
2. **Copy the token immediately** - you won't be able to see it again

The token will look like: `ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`

## Configuring the Token

### Option 1: Environment Variable (Recommended)

```bash
export GITHUB_TOKEN="ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

Add this to your shell profile (`~/.bashrc`, `~/.zshrc`) or a project-specific `.env` file.

### Option 2: Pass Per-Command

```bash
GITHUB_TOKEN="ghp_xxx..." prow-failure-analysis analyze --post-comment ...
```

## Verifying Your Token

Test that your token works:

```bash
export GITHUB_TOKEN="ghp_xxx..."

# Test API access
curl -H "Authorization: token $GITHUB_TOKEN" https://api.github.com/user

# Expected: JSON with your user info
```

## Security Best Practices

1. **Never commit tokens** - Add `.env` to `.gitignore`
2. **Use minimal scopes** - Only `public_repo` if you don't need private repo access
3. **Rotate regularly** - Set expiration and regenerate periodically
4. **Use secrets managers** - For production/CI environments, use proper secrets management (HashiCorp Vault, AWS Secrets Manager, GitHub Secrets, etc.)
5. **Revoke when done** - If token is compromised, revoke immediately at [https://github.com/settings/tokens](https://github.com/settings/tokens)
