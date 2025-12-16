from prow_failure_analysis.parsing.xunit_models import FailedTest


class TestFailedTest:
    """Tests for FailedTest dataclass properties with custom logic."""

    def test_test_identifier_with_class_name(self) -> None:
        """Test test_identifier includes class name when present."""
        failed_test = FailedTest(
            test_name="test_method",
            class_name="com.example.TestClass",
            test_id=None,
            failure_type=None,
            failure_message=None,
            failure_content=None,
            error_type=None,
            error_message=None,
            error_content=None,
            system_out=None,
            system_err=None,
            source_file="test.xml",
        )
        assert failed_test.test_identifier == "com.example.TestClass.test_method"

    def test_test_identifier_without_class_name(self) -> None:
        """Test test_identifier returns just test name when class_name is None."""
        failed_test = FailedTest(
            test_name="test_function",
            class_name=None,
            test_id=None,
            failure_type=None,
            failure_message=None,
            failure_content=None,
            error_type=None,
            error_message=None,
            error_content=None,
            system_out=None,
            system_err=None,
            source_file="test.xml",
        )
        assert failed_test.test_identifier == "test_function"

    def test_combined_failure_info_with_failure(self) -> None:
        """Test combined_failure_info formats failure type and message."""
        failed_test = FailedTest(
            test_name="test",
            class_name=None,
            test_id=None,
            failure_type="AssertionError",
            failure_message="Expected true but got false",
            failure_content=None,
            error_type=None,
            error_message=None,
            error_content=None,
            system_out=None,
            system_err=None,
            source_file="test.xml",
        )
        assert failed_test.combined_failure_info == "AssertionError: Expected true but got false"

    def test_combined_failure_info_with_error(self) -> None:
        """Test combined_failure_info falls back to error when no failure."""
        failed_test = FailedTest(
            test_name="test",
            class_name=None,
            test_id=None,
            failure_type=None,
            failure_message=None,
            failure_content=None,
            error_type="RuntimeError",
            error_message="Unexpected exception",
            error_content=None,
            system_out=None,
            system_err=None,
            source_file="test.xml",
        )
        assert failed_test.combined_failure_info == "RuntimeError: Unexpected exception"

    def test_combined_failure_info_failure_over_error(self) -> None:
        """Test that combined_failure_info prefers failure over error."""
        failed_test = FailedTest(
            test_name="test",
            class_name=None,
            test_id=None,
            failure_type="AssertionError",
            failure_message="Assertion failed",
            failure_content=None,
            error_type="RuntimeError",
            error_message="Runtime error",
            error_content=None,
            system_out=None,
            system_err=None,
            source_file="test.xml",
        )
        assert failed_test.combined_failure_info == "AssertionError: Assertion failed"

    def test_combined_failure_info_unknown(self) -> None:
        """Test combined_failure_info returns 'Unknown failure' as final fallback."""
        failed_test = FailedTest(
            test_name="test",
            class_name=None,
            test_id=None,
            failure_type=None,
            failure_message=None,
            failure_content=None,
            error_type=None,
            error_message=None,
            error_content=None,
            system_out=None,
            system_err=None,
            source_file="test.xml",
        )
        assert failed_test.combined_failure_info == "Unknown failure"

    def test_combined_details_includes_all_sections(self) -> None:
        """Test combined_details includes all non-None detail fields."""
        failed_test = FailedTest(
            test_name="test",
            class_name=None,
            test_id=None,
            failure_type=None,
            failure_message=None,
            failure_content="Failure traceback",
            error_type=None,
            error_message=None,
            error_content="Error traceback",
            system_out="Console output",
            system_err="Error output",
            source_file="test.xml",
        )
        details = failed_test.combined_details
        assert "--- Failure Content ---" in details and "Failure traceback" in details
        assert "--- Error Content ---" in details and "Error traceback" in details
        assert "--- System Out ---" in details and "Console output" in details
        assert "--- System Err ---" in details and "Error output" in details

    def test_combined_details_no_details(self) -> None:
        """Test combined_details returns fallback message when all fields are None."""
        failed_test = FailedTest(
            test_name="test",
            class_name=None,
            test_id=None,
            failure_type=None,
            failure_message=None,
            failure_content=None,
            error_type=None,
            error_message=None,
            error_content=None,
            system_out=None,
            system_err=None,
            source_file="test.xml",
        )
        assert failed_test.combined_details == "No additional details available"
