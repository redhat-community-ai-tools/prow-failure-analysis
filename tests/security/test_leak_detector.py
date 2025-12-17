from prow_failure_analysis.security.leak_detector import LeakDetector


class TestLeakDetector:
    """Test suite for LeakDetector class."""

    def test_initialization(self) -> None:
        """Test that LeakDetector initializes correctly."""
        detector = LeakDetector()
        assert len(detector.plugins) > 0

    def test_sanitize_empty_text(self) -> None:
        """Test sanitization of empty text."""
        detector = LeakDetector()
        assert detector.sanitize_text("") == ""

    def test_sanitize_text_without_secrets(self) -> None:
        """Test that clean text passes through unchanged."""
        detector = LeakDetector()
        clean_text = "This is a normal log message with no secrets."
        assert detector.sanitize_text(clean_text) == clean_text

    def test_detect_aws_access_key(self) -> None:
        """Test detection of AWS access keys."""
        detector = LeakDetector()
        text = "AWS credentials: AKIAIOSFODNN7EXAMPLE"
        result = detector.sanitize_text(text)

        # Should contain a redaction label
        assert "[REDACTED:" in result
        # Should not contain the actual key
        assert "AKIAIOSFODNN7EXAMPLE" not in result

    def test_detect_github_token(self) -> None:
        """Test detection of GitHub tokens."""
        detector = LeakDetector()
        text = "GitHub token: ghp_1234567890abcdefghijklmnopqrstuvwxyz"
        result = detector.sanitize_text(text)

        # Should contain a redaction label
        assert "[REDACTED:" in result
        # Should not contain the actual token
        assert "ghp_1234567890abcdefghijklmnopqrstuvwxyz" not in result

    def test_detect_private_key(self) -> None:
        """Test detection of private keys."""
        detector = LeakDetector()
        text = """
        -----BEGIN RSA PRIVATE KEY-----
        MIIEpAIBAAKCAQEA1234567890abcdefghijklmnopqrstuvwxyz
        -----END RSA PRIVATE KEY-----
        """
        result = detector.sanitize_text(text)

        # Should contain a redaction label
        assert "[REDACTED:" in result
        # Should not contain the private key header
        assert "BEGIN RSA PRIVATE KEY" not in result

    def test_detect_jwt_token(self) -> None:
        """Test detection of JWT tokens."""
        detector = LeakDetector()
        # Simplified JWT-like structure
        jwt_token = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        )
        text = f"Authorization: Bearer {jwt_token}"
        result = detector.sanitize_text(text)

        # Should contain a redaction label
        assert "[REDACTED:" in result
        # Should not contain the JWT
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in result

    def test_multiple_secrets_in_text(self) -> None:
        """Test detection of multiple secrets in the same text."""
        detector = LeakDetector()
        text = """
        AWS Key: AKIAIOSFODNN7EXAMPLE
        GitHub Token: ghp_1234567890abcdefghijklmnopqrstuvwxyz
        Some normal text here
        """
        result = detector.sanitize_text(text)

        # Should contain multiple redaction labels
        assert result.count("[REDACTED:") >= 2
        # Should not contain any of the secrets
        assert "AKIAIOSFODNN7EXAMPLE" not in result
        assert "ghp_1234567890abcdefghijklmnopqrstuvwxyz" not in result
        # Should preserve normal text
        assert "Some normal text here" in result

    def test_no_false_positives_on_common_strings(self) -> None:
        """Test that common strings are not incorrectly flagged as secrets."""
        detector = LeakDetector()

        # Common strings that should NOT be flagged
        safe_texts = [
            "user@example.com",
            "http://example.com/api/v1/endpoint",
            "commit hash: abc123def456",
            "version: 1.2.3",
            "port: 8080",
            "timeout: 30s",
        ]

        for text in safe_texts:
            result = detector.sanitize_text(text)
            # These should pass through unchanged (no redactions)
            assert result == text, f"False positive on: {text}"
