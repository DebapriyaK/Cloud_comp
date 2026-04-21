"""
run_tests.py
Automated test runner for carbon_analyzer.py.

Runs the analyzer against each test file, checks finding counts and
confidence levels against expected values, and reports pass/fail.
"""
import sys
import os

# Make sure we can import from the parent directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from carbon_analyzer import analyze

TEST_DIR = os.path.dirname(os.path.abspath(__file__))

PASS = "[PASS]"
FAIL = "[FAIL]"

results = []


def check(label, condition, detail=""):
    status = PASS if condition else FAIL
    msg = f"  {status}  {label}"
    if detail:
        msg += f"  ({detail})"
    print(msg)
    results.append(condition)
    return condition


def run_all():
    print()
    print("=" * 60)
    print("Carbon Analyzer -- Test Suite")
    print("=" * 60)

    # ------------------------------------------------------------------
    # TEST 1: User scenario
    # ------------------------------------------------------------------
    print("\n--- Test 1: User Scenario (parameter vs literal list) ---")
    f1 = analyze(os.path.join(TEST_DIR, "test_user_scenario.py"))

    check("Total findings == 2",
          len(f1) == 2,
          f"got {len(f1)}")

    confs = [f.confidence for f in f1]
    check("One CONFIRMED finding",
          confs.count("CONFIRMED") == 1,
          f"CONFIRMED count = {confs.count('CONFIRMED')}")

    check("One POSSIBLE finding",
          confs.count("POSSIBLE") == 1,
          f"POSSIBLE count = {confs.count('POSSIBLE')}")

    check("CONFIRMED finding is for REGISTERED (known list)",
          any(f.confidence == "CONFIRMED" and "REGISTERED" in f.description
              for f in f1),
          "check REGISTERED flagged as CONFIRMED")

    check("POSSIBLE finding is for allowed_members (parameter)",
          any(f.confidence == "POSSIBLE" and "allowed_members" in f.description
              for f in f1),
          "check parameter flagged as POSSIBLE")

    # All findings should have CO2 data (dataset covers G5 at N=100K)
    check("All findings have CO2 data from dataset",
          all(f.dirty_co2 is not None for f in f1))

    # ------------------------------------------------------------------
    # TEST 2: No findings on already-optimized code
    # ------------------------------------------------------------------
    print("\n--- Test 2: No False Positives on Clean Code ---")
    f2 = analyze(os.path.join(TEST_DIR, "test_no_findings.py"))

    check("Zero findings on clean code",
          len(f2) == 0,
          f"got {len(f2)} findings: {[x.dirty_op + '@' + str(x.line) for x in f2]}")

    # ------------------------------------------------------------------
    # TEST 3: Confidence level discrimination
    # ------------------------------------------------------------------
    print("\n--- Test 3: Confidence Level Discrimination ---")
    f3 = analyze(os.path.join(TEST_DIR, "test_confidence_levels.py"))

    check("Exactly 2 findings (set_c should produce no finding)",
          len(f3) == 2,
          f"got {len(f3)}")

    check("list_a fires as CONFIRMED",
          any(f.confidence == "CONFIRMED" and "list_a" in f.description
              for f in f3))

    check("param_b fires as POSSIBLE",
          any(f.confidence == "POSSIBLE" and "param_b" in f.description
              for f in f3))

    check("set_c does NOT fire",
          not any("set_c" in f.description for f in f3),
          "set should never generate a G5 finding")

    check("Reduction % present for CONFIRMED finding",
          any(f.confidence == "CONFIRMED" and
              f.reduction_pct is not None and f.reduction_pct > 0
              for f in f3))

    # ------------------------------------------------------------------
    # TEST 4: demo_dirty.py -- all 8 patterns
    # ------------------------------------------------------------------
    print("\n--- Test 4: Full Pattern Coverage (demo_dirty.py) ---")
    demo_path = os.path.join(TEST_DIR, "..", "demo_dirty.py")
    f4 = analyze(os.path.join(TEST_DIR, demo_path))

    groups_found = {f.group_label for f in f4}
    for expected_group in [
        "G1 . Summation",
        "G2 . Filtering",
        "G3 . Transformation",
        "G4 . Recursion",
        "G5 . Membership",
        "G6 . String Building",
        "G7 . Queue Operations",
    ]:
        check(f"Group detected: {expected_group}",
              expected_group in groups_found)

    check("At least one CONFIRMED and one POSSIBLE",
          any(f.confidence == "CONFIRMED" for f in f4) and
          any(f.confidence == "POSSIBLE"  for f in f4))

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print()
    print("=" * 60)
    passed = sum(results)
    total  = len(results)
    print(f"  {passed}/{total} checks passed")
    if passed == total:
        print("  All tests passed.")
    else:
        print(f"  {total - passed} check(s) FAILED -- review output above.")
    print("=" * 60)
    print()
    return passed == total


if __name__ == "__main__":
    ok = run_all()
    sys.exit(0 if ok else 1)
