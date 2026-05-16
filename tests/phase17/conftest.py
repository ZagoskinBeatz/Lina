"""
Phase 17 tests — conftest.

Many tests in this directory are standalone scripts with a
`def test(name, condition)` helper — not pytest-compatible.
They are skipped during collection.
"""

# Files that are standalone scripts (not pytest tests)
_STANDALONE_SCRIPTS = [
    "test_anomaly_contract.py",
    "test_routing_metrics_contract.py",
    "test_web_weather_url_encoding.py",
    "test_no_invalid_attribute_access.py",
    "test_architecture_stability.py",
]

# Files that import runtime_v2 modules deleted in Phase 28
_RUNTIME_V2_FILES = [
    "test_memory_failure.py",
    "test_redteam4.py",
    "test_concurrency_soak.py",
    "test_os_campaign.py",
]

collect_ignore_glob = _STANDALONE_SCRIPTS + _RUNTIME_V2_FILES
