import tempfile
from pathlib import Path

from prow_failure_analysis.processing.preprocessor import CHARS_PER_TOKEN, LogPreprocessor


class TestLogPreprocessor:
    """Tests for LogPreprocessor custom logic."""

    def test_estimate_tokens_fallback(self, mocker):
        """Test token estimation falls back to char-based calculation."""
        mock_vectorizer = mocker.Mock(spec=[])  # No model attribute
        mocker.patch("prow_failure_analysis.processing.preprocessor.create_vectorizer", return_value=mock_vectorizer)
        preprocessor = LogPreprocessor()

        text = "a" * 400
        tokens = preprocessor._estimate_tokens(text)

        assert tokens == 400 // CHARS_PER_TOKEN

    def test_estimate_tokens_with_tokenizer(self, mocker):
        """Test token estimation uses model tokenizer when available."""
        mock_vectorizer = mocker.Mock()
        mock_vectorizer.model.tokenizer.encode.return_value = [1, 2, 3, 4, 5]
        mocker.patch("prow_failure_analysis.processing.preprocessor.create_vectorizer", return_value=mock_vectorizer)

        preprocessor = LogPreprocessor()
        tokens = preprocessor._estimate_tokens("test text")

        assert tokens == 5

    def test_calculate_max_line_tokens_empty_lines(self, mocker):
        """Test max line tokens returns default for empty lines."""
        mocker.patch("prow_failure_analysis.processing.preprocessor.create_vectorizer")
        preprocessor = LogPreprocessor()

        max_tokens = preprocessor._calculate_max_line_tokens([])

        assert max_tokens == 50

    def test_calculate_max_line_tokens_samples_lines(self, mocker):
        """Test max line tokens samples and finds maximum."""
        mocker.patch("prow_failure_analysis.processing.preprocessor.create_vectorizer")
        preprocessor = LogPreprocessor()
        preprocessor._estimate_tokens = mocker.Mock(side_effect=lambda x: len(x) // CHARS_PER_TOKEN)

        lines = ["short", "a" * 100, "medium text here", "a" * 200, "tiny"]
        max_tokens = preprocessor._calculate_max_line_tokens(lines)

        assert max_tokens == 200 // CHARS_PER_TOKEN

    def test_preprocess_file_not_found(self, mocker):
        """Test preprocess_file returns empty string for missing file."""
        mocker.patch("prow_failure_analysis.processing.preprocessor.create_vectorizer")
        preprocessor = LogPreprocessor()

        result = preprocessor.preprocess_file("/nonexistent/path.log")

        assert result == ""

    def test_preprocess_file_below_threshold(self, mocker):
        """Test preprocess_file skips preprocessing for small files."""
        mocker.patch("prow_failure_analysis.processing.preprocessor.create_vectorizer")
        preprocessor = LogPreprocessor()
        preprocessor.size_threshold = 1000

        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log") as f:
            f.write("small log content")
            temp_path = f.name

        try:
            result = preprocessor.preprocess_file(temp_path)
            assert result == "small log content"
        finally:
            Path(temp_path).unlink()

    def test_preprocess_file_under_token_limit(self, mocker):
        """Test preprocess_file skips preprocessing when under token limit."""
        mocker.patch("prow_failure_analysis.processing.preprocessor.create_vectorizer")
        preprocessor = LogPreprocessor()
        preprocessor.size_threshold = 100
        preprocessor.max_tokens = 1000

        content = "a" * 2000  # ~500 tokens, well under limit
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log") as f:
            f.write(content)
            temp_path = f.name

        try:
            result = preprocessor.preprocess_file(temp_path)
            assert result == content
        finally:
            Path(temp_path).unlink()

    def test_init_with_config(self, mocker):
        """Test initialization with config auto-detects settings."""
        mocker.patch("prow_failure_analysis.processing.preprocessor.create_vectorizer")
        mock_config = mocker.Mock()
        mock_config.cordon_device = "cuda"
        mock_config.detect_model_context_limit.return_value = 100_000

        preprocessor = LogPreprocessor(config=mock_config)

        assert preprocessor.device == "cuda"
        assert preprocessor.max_tokens == 20_000  # 20% of 100k
        assert preprocessor.size_threshold == 20_000  # 5% * 4 chars/token

    def test_init_without_config(self, mocker):
        """Test initialization without config uses defaults."""
        mocker.patch("prow_failure_analysis.processing.preprocessor.create_vectorizer")

        preprocessor = LogPreprocessor()

        assert preprocessor.device == "cpu"
        assert preprocessor.max_tokens == 100_000
        assert preprocessor.size_threshold == 50_000

    def test_preprocess_memory_to_file(self, mocker):
        """Test preprocess method writes to temp file and calls preprocess_file."""
        mocker.patch("prow_failure_analysis.processing.preprocessor.create_vectorizer")
        preprocessor = LogPreprocessor()
        preprocessor.size_threshold = 1000

        result = preprocessor.preprocess("small content")

        assert result == "small content"
