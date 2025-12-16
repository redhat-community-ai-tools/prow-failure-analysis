"""Unit tests for main module."""

from prow_failure_analysis.config import Config
from prow_failure_analysis.main import configure_dspy


class TestConfigureDSPy:
    """Tests for DSPy configuration."""

    def test_configure_dspy_with_api_key(self, mocker):
        """Test configure_dspy includes api_key when set."""
        mock_lm = mocker.patch("prow_failure_analysis.main.dspy.LM")
        mocker.patch("prow_failure_analysis.main.dspy.configure")

        config = Config()
        config.llm_provider = "openai"
        config.llm_model = "gpt-4"
        config.llm_api_key = "test-key"

        configure_dspy(config)

        mock_lm.assert_called_once_with(model="openai/gpt-4", api_key="test-key")

    def test_configure_dspy_with_base_url(self, mocker):
        """Test configure_dspy includes api_base when set."""
        mock_lm = mocker.patch("prow_failure_analysis.main.dspy.LM")
        mocker.patch("prow_failure_analysis.main.dspy.configure")

        config = Config()
        config.llm_provider = "openai"
        config.llm_model = "gpt-4"
        config.llm_api_key = "test-key"
        config.llm_base_url = "https://custom.api.com"

        configure_dspy(config)

        mock_lm.assert_called_once_with(model="openai/gpt-4", api_key="test-key", api_base="https://custom.api.com")

    def test_configure_dspy_ollama_default_url(self, mocker):
        """Test configure_dspy uses default URL for ollama when not set."""
        mock_lm = mocker.patch("prow_failure_analysis.main.dspy.LM")
        mocker.patch("prow_failure_analysis.main.dspy.configure")

        config = Config()
        config.llm_provider = "ollama"
        config.llm_model = "llama3"
        config.llm_api_key = ""

        configure_dspy(config)

        mock_lm.assert_called_once_with(model="ollama/llama3", api_base="http://localhost:11434")
