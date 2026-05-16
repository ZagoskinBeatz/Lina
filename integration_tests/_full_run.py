#!/usr/bin/env python3
"""Full 45-test integration suite run."""
import sys, os, time, signal, faulthandler

# Печать stack trace по SIGUSR1 (для диагностики зависаний)
faulthandler.enable()

# Глобальный таймаут: 20 минут (7B модель на CPU — ~20сек/тест × 45)
GLOBAL_TIMEOUT = 1200  # секунд

def timeout_handler(signum, frame):
    print(f"\n⛔ GLOBAL TIMEOUT ({GLOBAL_TIMEOUT}s) — принудительный выход",
          flush=True)
    sys.exit(2)


if __name__ == "__main__":
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(GLOBAL_TIMEOUT)

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
    os.chdir(os.path.join(os.path.dirname(__file__), '..', '..'))

    from lina.integration_tests.framework import IntegrationRunner
    from lina.integration_tests.test_cases import collect_all_tests
    from lina.integration_tests.sandbox_env import SandboxEnvironment
    from lina.integration_tests.reporter import TestReporter

    tests = collect_all_tests()
    print(f"Total tests: {len(tests)}", flush=True)
    wall_start = time.time()

    with SandboxEnvironment() as sandbox:
        runner = IntegrationRunner(sandbox_env=sandbox, dry_run=False, verbose=True)
        results = runner.run_all(test_cases=tests)

        reporter = TestReporter(results)
        report = reporter.generate_report(
            output_path='lina/logs/integration_report.json'
        )
        reporter.print_report(report)

        # Summary of failed tests with responses
        failed = [r for r in results if r.status == "failed"]
        if failed:
            print("\n=== FAILED TEST DETAILS ===")
            for r in failed:
                print(f"\n{r.test_id}: {r.name}")
                print(f"  Input: {r.input_text[:100]}")
                resp = r.llm_response[:200] if r.llm_response else "(empty)"
                print(f"  Response: {resp}")
                print(f"  Error: {r.error_message}")

        passed = sum(1 for r in results if r.status in ("passed", "warning"))
        total = len(results)
        wall_elapsed = time.time() - wall_start
        print(f"\n{'='*60}", flush=True)
        print(f"FINAL: {passed}/{total} passed ({100*passed/total:.0f}%)", flush=True)
