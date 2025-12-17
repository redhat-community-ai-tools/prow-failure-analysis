import logging
import tempfile
from pathlib import Path
from typing import Any

from cordon import AnalysisConfig, SemanticLogAnalyzer
from cordon.embedding import create_vectorizer

from ..constants import CHARS_PER_TOKEN

logger = logging.getLogger(__name__)


class LogPreprocessor:
    """Reduces log size using semantic anomaly detection while preserving critical information."""

    def __init__(
        self,
        config: Any = None,
        device: str | None = None,
        last_lines_to_keep: int = 15,
        safety_margin: float = 0.4,
        k_neighbors: int = 5,
        min_percentile: float = 0.05,
        model_name: str = "all-MiniLM-L6-v2",
        backend: str = "sentence-transformers",
    ) -> None:
        """Initialize the preprocessor.

        Args:
            config: Config object for auto-detecting LLM context limits
            device: Device for cordon (cpu/cuda/mps), auto-detected from config if not specified
            last_lines_to_keep: Number of final lines to always preserve
            safety_margin: Window size safety margin (0.0-1.0, lower = more conservative)
            k_neighbors: Number of neighbors for anomaly detection
            min_percentile: Minimum percentage of lines to keep
            model_name: Sentence-transformers model name
            backend: Embedding backend ('sentence-transformers' or 'llama-cpp')
        """
        self.config = config
        self.last_lines_to_keep = last_lines_to_keep
        self.safety_margin = safety_margin
        self.k_neighbors = k_neighbors
        self.min_percentile = min_percentile
        self.model_name = model_name
        self.backend = backend

        self.device = config.cordon_device if config and device is None else (device or "cpu")

        if config:
            downstream_context = config.detect_model_context_limit()
            self.max_tokens = int(downstream_context * 0.20)
            self.size_threshold = int(downstream_context * 0.05 * CHARS_PER_TOKEN)
            logger.info(f"Auto-detected downstream LLM context: {downstream_context:,} tokens")
            logger.info(f"Target max_tokens per log: {self.max_tokens:,}, threshold: {self.size_threshold:,} bytes")
        else:
            self.max_tokens = 100_000
            self.size_threshold = 50_000
            logger.info("No config provided, using default token limits")

        cordon_config = AnalysisConfig(device=self.device, model_name=model_name, backend=backend)
        self.vectorizer = create_vectorizer(cordon_config)

        if hasattr(self.vectorizer, "model") and hasattr(self.vectorizer.model, "max_seq_length"):
            self.model_max_sequence_tokens = self.vectorizer.model.max_seq_length
            logger.info(f"Embedding model max sequence length: {self.model_max_sequence_tokens} tokens")
        else:
            self.model_max_sequence_tokens = 256
            logger.warning(f"Using default embedding model max sequence length: {self.model_max_sequence_tokens}")

    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count using model's tokenizer or fallback to char-based estimation."""
        if hasattr(self.vectorizer, "model") and hasattr(self.vectorizer.model, "tokenizer"):
            try:
                tokens = self.vectorizer.model.tokenizer.encode(text, add_special_tokens=True)
                return len(tokens)
            except Exception:
                pass
        return len(text) // CHARS_PER_TOKEN

    def preprocess_file(self, log_path: str, step_name: str = "unknown", max_tokens: int | None = None) -> str:
        """Preprocess a log file, applying cordon if it exceeds size threshold.

        Args:
            log_path: Path to log file
            step_name: Step name for logging
            max_tokens: Target token count (defaults to instance max_tokens)

        Returns:
            Preprocessed log content
        """
        max_tokens = max_tokens or self.max_tokens
        log_path_obj = Path(log_path)

        if not log_path_obj.exists():
            logger.warning(f"Step {step_name}: log file not found at {log_path}")
            return ""

        log_size = log_path_obj.stat().st_size

        if log_size < self.size_threshold:
            logger.debug(f"Step {step_name}: {log_size} bytes, skipping preprocessing")
            return log_path_obj.read_text()

        quick_tokens = log_size // CHARS_PER_TOKEN
        if quick_tokens <= max_tokens * 0.8:
            logger.debug(f"Step {step_name}: ~{quick_tokens} tokens, under limit")
            return log_path_obj.read_text()

        log_content = log_path_obj.read_text()

        if len(log_content) > 1_000_000:
            sample = log_content[:10_000]
            sample_tokens = self._estimate_tokens(sample)
            estimated_tokens = int((sample_tokens / 10_000) * len(log_content))
        else:
            estimated_tokens = self._estimate_tokens(log_content)

        target_percentile = max(self.min_percentile, max_tokens / estimated_tokens)
        pct = target_percentile * 100
        logger.info(f"Step {step_name}: {estimated_tokens} tokens (limit: {max_tokens}), keeping top {pct:.1f}%")

        try:
            lines = [line.rstrip("\n") for line in log_content.split("\n")]

            last_lines = lines[-self.last_lines_to_keep :] if len(lines) > self.last_lines_to_keep else []
            content_to_process = "\n".join(lines[: -self.last_lines_to_keep]) if last_lines else "\n".join(lines)

            max_line_tokens = self._calculate_max_line_tokens(lines)
            window_size = max(1, int((self.model_max_sequence_tokens * self.safety_margin) / max(1, max_line_tokens)))

            logger.info(f"Step {step_name}: max_line_tokens={max_line_tokens}, window_size={window_size}")

            config = AnalysisConfig(
                window_size=window_size,
                k_neighbors=self.k_neighbors,
                anomaly_percentile=target_percentile,
                device=self.device,
                backend="sentence-transformers",
            )
            analyzer = SemanticLogAnalyzer(config)

            with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as tmp_file:
                tmp_path = Path(tmp_file.name)
                tmp_file.write(content_to_process)

            try:
                reduced_content = analyzer.analyze_file(tmp_path)
            finally:
                tmp_path.unlink()

            if last_lines:
                reduced_content = reduced_content.rstrip() + "\n\n--- FINAL OUTPUT ---\n" + "\n".join(last_lines)

            final_tokens = self._estimate_tokens(reduced_content)
            reduction_pct = ((log_size - len(reduced_content)) / log_size) * 100

            logger.info(f"Step {step_name}: reduced {estimated_tokens} → {final_tokens} tokens ({reduction_pct:.1f}%)")

            result: str = reduced_content
            return result

        except Exception as e:
            logger.error(f"Step {step_name}: preprocessing failed: {e}, returning original")
            return log_path_obj.read_text()

    def _calculate_max_line_tokens(self, lines: list[str]) -> int:
        """Calculate max token count from sampled lines."""
        non_empty = [line for line in lines if line.strip()]
        if not non_empty:
            return 50

        sample_size = min(100, len(non_empty))
        step = max(1, len(non_empty) // sample_size)
        sampled = non_empty[::step][:sample_size]

        return max((self._estimate_tokens(line) for line in sampled), default=50)

    def preprocess(self, log_content: str, step_name: str = "unknown", max_tokens: int | None = None) -> str:
        """Preprocess log content from memory.

        Args:
            log_content: Raw log content
            step_name: Step name for logging
            max_tokens: Target token count (defaults to instance max_tokens)

        Returns:
            Preprocessed log content
        """
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as tmp_file:
            tmp_path = tmp_file.name
            tmp_file.write(log_content)

        try:
            return self.preprocess_file(tmp_path, step_name, max_tokens)
        finally:
            Path(tmp_path).unlink(missing_ok=True)
