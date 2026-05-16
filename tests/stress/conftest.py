# conftest.py — skip tests that import deleted runtime_v2 modules
collect_ignore_glob = [
    "test_50_parallel_sessions.py",
    "test_100_parallel_requests.py",
    "test_burst_rate_limit.py",
    "test_token_budget_isolation.py",
    "test_session_memory_isolation.py",
    "run_all_stress.py",
]
