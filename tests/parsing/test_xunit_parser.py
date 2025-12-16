import pytest

from prow_failure_analysis.parsing.xunit_parser import XUnitParser


class TestXUnitParser:
    """Tests for XUnitParser class."""

    @pytest.fixture
    def parser(self) -> XUnitParser:
        """Create a parser instance for tests."""
        return XUnitParser()

    def test_parse_simple_failure(self, parser: XUnitParser) -> None:
        """Test parsing a simple test case with a failure."""
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="TestSuite" tests="1" failures="1">
    <testcase name="test_simple" classname="com.example.TestClass" id="test-001">
        <failure type="AssertionError" message="Expected 5 but got 3">
            Traceback (most recent call last):
              File "test.py", line 10, in test_simple
                assert 5 == 3
            AssertionError: Expected 5 but got 3
        </failure>
    </testcase>
</testsuite>"""

        results = parser.parse_xunit_file(xml_content, "junit.xml")

        assert len(results) == 1
        failed_test = results[0]
        assert failed_test.test_name == "test_simple"
        assert failed_test.class_name == "com.example.TestClass"
        assert failed_test.test_id == "test-001"
        assert failed_test.failure_type == "AssertionError"
        assert failed_test.failure_message == "Expected 5 but got 3"
        assert "Traceback" in failed_test.failure_content
        assert failed_test.error_type is None
        assert failed_test.source_file == "junit.xml"

    def test_parse_error(self, parser: XUnitParser) -> None:
        """Test parsing a test case with an error."""
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="TestSuite" tests="1" errors="1">
    <testcase name="test_error" classname="ErrorTest">
        <error type="RuntimeError" message="Something went wrong">
            RuntimeError: Something went wrong
            at line 42
        </error>
    </testcase>
</testsuite>"""

        results = parser.parse_xunit_file(xml_content, "errors.xml")

        assert len(results) == 1
        failed_test = results[0]
        assert failed_test.test_name == "test_error"
        assert failed_test.class_name == "ErrorTest"
        assert failed_test.error_type == "RuntimeError"
        assert failed_test.error_message == "Something went wrong"
        assert "RuntimeError" in failed_test.error_content
        assert failed_test.failure_type is None

    def test_parse_multiple_failures(self, parser: XUnitParser) -> None:
        """Test parsing multiple failed test cases."""
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="TestSuite" tests="3" failures="2">
    <testcase name="test_one" classname="TestClass">
        <failure type="AssertionError" message="Test one failed">
            Details for test one
        </failure>
    </testcase>
    <testcase name="test_two" classname="TestClass">
        <!-- Passing test, should be ignored -->
    </testcase>
    <testcase name="test_three" classname="TestClass">
        <failure type="AssertionError" message="Test three failed">
            Details for test three
        </failure>
    </testcase>
</testsuite>"""

        results = parser.parse_xunit_file(xml_content, "multiple.xml")

        assert len(results) == 2
        assert results[0].test_name == "test_one"
        assert results[1].test_name == "test_three"

    def test_parse_nested_testsuites(self, parser: XUnitParser) -> None:
        """Test parsing with nested testsuites structure."""
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites>
    <testsuite name="Suite1">
        <testcase name="test_a" classname="Suite1.Tests">
            <failure message="Failed">Details A</failure>
        </testcase>
    </testsuite>
    <testsuite name="Suite2">
        <testcase name="test_b" classname="Suite2.Tests">
            <error message="Error">Details B</error>
        </testcase>
    </testsuite>
</testsuites>"""

        results = parser.parse_xunit_file(xml_content, "nested.xml")

        assert len(results) == 2
        assert results[0].test_name == "test_a"
        assert results[1].test_name == "test_b"

    def test_parse_with_system_out_and_err(self, parser: XUnitParser) -> None:
        """Test parsing test case with system-out and system-err."""
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="TestSuite" tests="1" failures="1">
    <testcase name="test_with_output" classname="OutputTest">
        <failure message="Test failed">Failure details</failure>
        <system-out>Console output here</system-out>
        <system-err>Error output here</system-err>
    </testcase>
</testsuite>"""

        results = parser.parse_xunit_file(xml_content, "output.xml")

        assert len(results) == 1
        failed_test = results[0]
        assert failed_test.system_out == "Console output here"
        assert failed_test.system_err == "Error output here"

    def test_parse_minimal_test_case(self, parser: XUnitParser) -> None:
        """Test parsing a minimal test case without optional attributes."""
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="MinimalSuite">
    <testcase name="minimal_test">
        <failure>No attributes</failure>
    </testcase>
</testsuite>"""

        results = parser.parse_xunit_file(xml_content, "minimal.xml")

        assert len(results) == 1
        failed_test = results[0]
        assert failed_test.test_name == "minimal_test"
        assert failed_test.class_name is None
        assert failed_test.test_id is None
        assert failed_test.failure_type is None
        assert failed_test.failure_message is None
        # The text "No attributes" should be captured as failure_content
        assert failed_test.failure_content == "No attributes"

    def test_parse_no_failures(self, parser: XUnitParser) -> None:
        """Test parsing XML with no failed tests."""
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="PassingSuite" tests="2" failures="0">
    <testcase name="test_one" classname="PassingTests"/>
    <testcase name="test_two" classname="PassingTests"/>
</testsuite>"""

        results = parser.parse_xunit_file(xml_content, "passing.xml")

        assert len(results) == 0

    def test_parse_empty_text_elements(self, parser: XUnitParser) -> None:
        """Test that empty or whitespace-only text elements return None."""
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="TestSuite">
    <testcase name="test_empty" classname="EmptyTest">
        <failure type="Failure" message="Message">   </failure>
        <system-out>   </system-out>
    </testcase>
</testsuite>"""

        results = parser.parse_xunit_file(xml_content, "empty.xml")

        assert len(results) == 1
        failed_test = results[0]
        # Empty/whitespace text should be None
        assert failed_test.failure_content is None
        assert failed_test.system_out is None

    def test_parse_invalid_xml(self, parser: XUnitParser) -> None:
        """Test that invalid XML returns empty list and doesn't crash."""
        xml_content = """This is not valid XML at all <broken>"""

        results = parser.parse_xunit_file(xml_content, "invalid.xml")

        assert len(results) == 0

    def test_parse_both_failure_and_error(self, parser: XUnitParser) -> None:
        """Test parsing a test case with both failure and error elements."""
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="TestSuite">
    <testcase name="test_both" classname="BothTest">
        <failure type="AssertionError" message="Assertion failed">
            Assertion details
        </failure>
        <error type="RuntimeError" message="Runtime error">
            Error details
        </error>
    </testcase>
</testsuite>"""

        results = parser.parse_xunit_file(xml_content, "both.xml")

        assert len(results) == 1
        failed_test = results[0]
        # Both failure and error should be captured
        assert failed_test.failure_type == "AssertionError"
        assert failed_test.failure_message == "Assertion failed"
        assert "Assertion details" in failed_test.failure_content
        assert failed_test.error_type == "RuntimeError"
        assert failed_test.error_message == "Runtime error"
        assert "Error details" in failed_test.error_content

    def test_parse_test_without_name_uses_unknown(self, parser: XUnitParser) -> None:
        """Test that test cases without a name attribute get 'unknown' as name."""
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="TestSuite">
    <testcase classname="NoNameTest">
        <failure message="Failed">Details</failure>
    </testcase>
</testsuite>"""

        results = parser.parse_xunit_file(xml_content, "noname.xml")

        assert len(results) == 1
        assert results[0].test_name == "unknown"
        assert results[0].class_name == "NoNameTest"

    def test_parse_empty_xml(self, parser: XUnitParser) -> None:
        """Test parsing empty/minimal valid XML."""
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="EmptySuite"/>"""

        results = parser.parse_xunit_file(xml_content, "empty.xml")

        assert len(results) == 0

    def test_parse_preserves_source_path(self, parser: XUnitParser) -> None:
        """Test that source_path is correctly preserved in results."""
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="TestSuite">
    <testcase name="test_one">
        <failure message="Failed">Details</failure>
    </testcase>
    <testcase name="test_two">
        <error message="Error">Details</error>
    </testcase>
</testsuite>"""

        source_path = "path/to/test/results.xml"
        results = parser.parse_xunit_file(xml_content, source_path)

        assert len(results) == 2
        assert all(test.source_file == source_path for test in results)

    def test_get_element_text_with_none(self, parser: XUnitParser) -> None:
        """Test _get_element_text with None element."""
        result = parser._get_element_text(None)
        assert result is None

    def test_parse_multiline_content(self, parser: XUnitParser) -> None:
        """Test parsing failure content with multiple lines."""
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="TestSuite">
    <testcase name="test_multiline" classname="MultilineTest">
        <failure type="AssertionError" message="Multiline failure">
            Line 1 of failure
            Line 2 of failure
            Line 3 of failure
        </failure>
    </testcase>
</testsuite>"""

        results = parser.parse_xunit_file(xml_content, "multiline.xml")

        assert len(results) == 1
        failed_test = results[0]
        # Content should be stripped but preserve internal structure
        assert "Line 1 of failure" in failed_test.failure_content
        assert "Line 2 of failure" in failed_test.failure_content
        assert "Line 3 of failure" in failed_test.failure_content
