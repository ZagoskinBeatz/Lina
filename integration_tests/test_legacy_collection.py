"""Pytest smoke tests for legacy integration scripts.

These scripts have their own mini-runner and are executed directly in CI.
The checks below ensure pytest does not try to collect their helper
`test(...)` functions as real pytest tests.
"""

from importlib import import_module


def _helper(module_name: str):
    module = import_module(f"lina.integration_tests.{module_name}")
    return getattr(module, "test")


def test_phase9_helper_hidden_from_pytest():
    assert getattr(_helper("test_phase9"), "__test__", True) is False


def test_phase10_helper_hidden_from_pytest():
    assert getattr(_helper("test_phase10"), "__test__", True) is False


def test_phase11_helper_hidden_from_pytest():
    assert getattr(_helper("test_phase11"), "__test__", True) is False


def test_phase12_helper_hidden_from_pytest():
    assert getattr(_helper("test_phase12"), "__test__", True) is False
