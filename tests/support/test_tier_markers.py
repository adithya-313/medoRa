"""Tests for tier marker enforcement plugin."""

import pytest

pytestmark = pytest.mark.unit


def test_missing_tier_marker_raises_usage_error(pytester: pytest.Pytester) -> None:
    """Verify that a test lacking any tier marker triggers a USAGE_ERROR.

    Purpose:
        Ensures that tests without an assigned tier marker cannot run.

    Inputs/Outputs:
        pytester: Pytest fixture for running isolated test instances.

    Key Logic:
        Writes a single test function without any tier marker.
        Invokes pytest with the tier_markers plugin and --strict-markers.
        Asserts that the exit code is USAGE_ERROR and nodeid is reported.

    Errors:
        AssertionError if exit code is not USAGE_ERROR or nodeid missing from output.
    """
    pytester.makepyfile(
        """
        def test_unmarked():
            pass
        """
    )
    result = pytester.runpytest("-p", "tests.support.tier_markers", "--strict-markers")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    combined_output = str(result.stdout) + "\n" + str(result.stderr)
    assert "test_unmarked" in combined_output


def test_multiple_tier_markers_raises_usage_error(pytester: pytest.Pytester) -> None:
    """Verify that a test with multiple tier markers triggers a USAGE_ERROR.

    Purpose:
        Enforces mutual exclusivity among tier markers.

    Inputs/Outputs:
        pytester: Pytest fixture for running isolated test instances.

    Key Logic:
        Writes a test function marked with both unit and integration tiers.
        Runs pytest with the tier_markers plugin.
        Asserts that the exit code is USAGE_ERROR.

    Errors:
        AssertionError if exit code is not USAGE_ERROR.
    """
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.unit
        @pytest.mark.integration
        def test_multi_marked():
            pass
        """
    )
    result = pytester.runpytest("-p", "tests.support.tier_markers", "--strict-markers")
    assert result.ret == pytest.ExitCode.USAGE_ERROR


def test_single_tier_marker_passes(pytester: pytest.Pytester) -> None:
    """Verify that a test with exactly one tier marker passes collection and executes.

    Purpose:
        Validates the happy path for tier marker compliance.

    Inputs/Outputs:
        pytester: Pytest fixture for running isolated test instances.

    Key Logic:
        Writes a test marked with @pytest.mark.unit.
        Runs pytest and verifies returncode OK and 1 passed test.

    Errors:
        AssertionError if returncode != OK or test did not pass.
    """
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.unit
        def test_valid_unit():
            assert True
        """
    )
    result = pytester.runpytest("-p", "tests.support.tier_markers", "--strict-markers")
    assert result.ret == pytest.ExitCode.OK
    result.assert_outcomes(passed=1)


def test_unknown_marker_typo_rejected(pytester: pytest.Pytester) -> None:
    """Verify that an unregistered marker typo is rejected under strict markers.

    Purpose:
        Ensures typos like 'unti' are caught immediately by pytest strict markers.

    Inputs/Outputs:
        pytester: Pytest fixture for running isolated test instances.

    Key Logic:
        Writes a test marked with unknown marker 'unti'.
        Runs pytest with --strict-markers.
        Asserts non-OK returncode and mentions of 'unti' in output.

    Errors:
        AssertionError if returncode is OK or 'unti' not in output.
    """
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.unti
        def test_typo():
            pass
        """
    )
    result = pytester.runpytest("-p", "tests.support.tier_markers", "--strict-markers")
    assert result.ret != pytest.ExitCode.OK
    combined_output = str(result.stdout) + "\n" + str(result.stderr)
    assert "unti" in combined_output


def test_marker_selection_filters_appropriately(pytester: pytest.Pytester) -> None:
    """Verify that -m filtering works alongside tier validation.

    Purpose:
        Ensures pytest -m filter executes requested tier while deselecting others.

    Inputs/Outputs:
        pytester: Pytest fixture for running isolated test instances.

    Key Logic:
        Writes one unit test and one integration test.
        Runs pytest with -m unit.
        Verifies 1 passed and 1 deselected outcome.

    Errors:
        AssertionError if outcomes do not match 1 passed and 1 deselected.
    """
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.unit
        def test_unit_target():
            assert True

        @pytest.mark.integration
        def test_integration_target():
            assert True
        """
    )
    result = pytester.runpytest("-p", "tests.support.tier_markers", "--strict-markers", "-m", "unit")
    assert result.ret == pytest.ExitCode.OK
    result.assert_outcomes(passed=1, deselected=1)


def test_unmarked_test_caught_before_marker_filter(pytester: pytest.Pytester) -> None:
    """Verify that tier checking runs before -m filtering deselects unmarked tests.

    Purpose:
        Guards tryfirst=True: the check must see tests before -m filters them out,
        otherwise an unlabelled test escapes CI's -m unit run.

    Inputs/Outputs:
        pytester: Pytest fixture for running isolated test instances.

    Key Logic:
        Writes a file with one unit-marked test and one unmarked test.
        Runs pytest with -m unit and tier_markers plugin.
        Asserts ret == pytest.ExitCode.USAGE_ERROR and that the unmarked test name appears in output.

    Errors:
        AssertionError if exit code is not USAGE_ERROR or unmarked test name is absent from output.
    """
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.unit
        def test_marked_unit():
            assert True

        def test_unmarked_escape():
            assert True
        """
    )
    result = pytester.runpytest("-p", "tests.support.tier_markers", "--strict-markers", "-m", "unit")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    combined_output = str(result.stdout) + "\n" + str(result.stderr)
    assert "test_unmarked_escape" in combined_output
