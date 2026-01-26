"""Tests for utility functions and decorators."""

from unittest.mock import Mock, patch

import pytest

from prow_failure_analysis.utils import retry_with_backoff


class TestRetryWithBackoff:
    """Tests for the retry_with_backoff decorator."""

    def test_successful_call_no_retry(self):
        """Test that successful calls don't trigger retries."""
        mock_func = Mock(return_value="success")
        decorated = retry_with_backoff()(mock_func)

        result = decorated()

        assert result == "success"
        assert mock_func.call_count == 1

    def test_transient_error_with_retry(self):
        """Test that transient errors trigger retries with exponential backoff."""
        mock_func = Mock(side_effect=[Exception("transient error"), Exception("still failing"), "success"])

        with patch("time.sleep") as mock_sleep:
            decorated = retry_with_backoff(max_retries=3, base_delay=2.0)(mock_func)
            result = decorated()

        assert result == "success"
        assert mock_func.call_count == 3
        # Should sleep 2s, then 4s (exponential backoff)
        assert mock_sleep.call_count == 2
        mock_sleep.assert_any_call(2.0)  # First retry: 2.0 * 2^0
        mock_sleep.assert_any_call(4.0)  # Second retry: 2.0 * 2^1

    def test_rate_limit_error_longer_delay(self):
        """Test that rate limit errors use longer delays."""
        mock_func = Mock(side_effect=[Exception("Rate limit exceeded"), "success"])

        with patch("time.sleep") as mock_sleep:
            decorated = retry_with_backoff(max_retries=3, rate_limit_delay=6.0)(mock_func)
            result = decorated()

        assert result == "success"
        assert mock_func.call_count == 2
        # Should use rate_limit_delay instead of base_delay
        mock_sleep.assert_called_once_with(6.0)  # 6.0 * 2^0

    def test_quota_error_detection(self):
        """Test that quota errors are detected as rate limits."""
        mock_func = Mock(side_effect=[Exception("You exceeded your quota"), "success"])

        with patch("time.sleep") as mock_sleep:
            decorated = retry_with_backoff(max_retries=3, rate_limit_delay=6.0)(mock_func)
            result = decorated()

        assert result == "success"
        mock_sleep.assert_called_once_with(6.0)

    def test_429_error_detection(self):
        """Test that 429 status codes are detected as rate limits."""
        mock_func = Mock(side_effect=[Exception("Error 429: Too Many Requests"), "success"])

        with patch("time.sleep") as mock_sleep:
            decorated = retry_with_backoff(max_retries=3, rate_limit_delay=6.0)(mock_func)
            result = decorated()

        assert result == "success"
        mock_sleep.assert_called_once_with(6.0)

    def test_context_error_no_retry(self):
        """Test that context window errors don't retry."""
        mock_func = Mock(side_effect=Exception("Context window exceeded"))

        decorated = retry_with_backoff(max_retries=3, context_errors_no_retry=True)(mock_func)

        with pytest.raises(Exception, match="Context window exceeded"):
            decorated()

        # Should fail immediately without retries
        assert mock_func.call_count == 1

    def test_context_error_with_retry_disabled(self):
        """Test that context errors can retry if configured."""
        mock_func = Mock(side_effect=[Exception("Context window exceeded"), "success"])

        with patch("time.sleep"):
            decorated = retry_with_backoff(max_retries=3, context_errors_no_retry=False)(mock_func)
            result = decorated()

        assert result == "success"
        assert mock_func.call_count == 2

    def test_max_retries_exhausted(self):
        """Test that errors are raised after max retries."""
        mock_func = Mock(side_effect=Exception("persistent error"))

        with patch("time.sleep"):
            decorated = retry_with_backoff(max_retries=3)(mock_func)

            with pytest.raises(Exception, match="persistent error"):
                decorated()

        assert mock_func.call_count == 3

    def test_exponential_backoff_calculation(self):
        """Test that exponential backoff is calculated correctly."""
        mock_func = Mock(side_effect=[Exception("error1"), Exception("error2"), Exception("error3"), "success"])

        with patch("time.sleep") as mock_sleep:
            decorated = retry_with_backoff(max_retries=4, base_delay=2.0)(mock_func)
            result = decorated()

        assert result == "success"
        assert mock_func.call_count == 4
        # Check exponential backoff: 2s, 4s, 8s
        assert mock_sleep.call_count == 3
        calls = [call[0][0] for call in mock_sleep.call_args_list]
        assert calls == [2.0, 4.0, 8.0]

    def test_rate_limit_exponential_backoff(self):
        """Test exponential backoff for rate limit errors."""
        mock_func = Mock(
            side_effect=[
                Exception("rate limit error 1"),
                Exception("rate limit error 2"),
                "success",
            ]
        )

        with patch("time.sleep") as mock_sleep:
            decorated = retry_with_backoff(max_retries=3, rate_limit_delay=6.0)(mock_func)
            result = decorated()

        assert result == "success"
        # Check rate limit exponential backoff: 6s, 12s
        assert mock_sleep.call_count == 2
        calls = [call[0][0] for call in mock_sleep.call_args_list]
        assert calls == [6.0, 12.0]

    def test_mixed_error_types(self):
        """Test handling of mixed error types."""
        mock_func = Mock(
            side_effect=[
                Exception("rate limit exceeded"),  # Rate limit: 6s delay
                Exception("transient error"),  # Transient: 2s delay (starts over at 0)
                "success",
            ]
        )

        with patch("time.sleep") as mock_sleep:
            decorated = retry_with_backoff(max_retries=3, base_delay=2.0, rate_limit_delay=6.0)(mock_func)
            result = decorated()

        assert result == "success"
        assert mock_func.call_count == 3
        # Each error type has its own backoff sequence
        calls = [call[0][0] for call in mock_sleep.call_args_list]
        assert calls[0] == 6.0  # First attempt: rate limit
        assert calls[1] == 4.0  # Second attempt: transient (2.0 * 2^1)

    def test_preserves_function_metadata(self):
        """Test that decorator preserves function metadata."""

        @retry_with_backoff()
        def example_func():
            """Example docstring."""
            return "result"

        assert example_func.__name__ == "example_func"
        assert example_func.__doc__ == "Example docstring."

    def test_works_with_arguments(self):
        """Test that decorator works with function arguments."""
        mock_func = Mock(return_value="success")

        decorated = retry_with_backoff()(mock_func)
        result = decorated("arg1", kwarg="value")

        assert result == "success"
        mock_func.assert_called_once_with("arg1", kwarg="value")
