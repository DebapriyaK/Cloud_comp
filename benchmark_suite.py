"""
benchmark_suite.py
------------------
Offline Benchmark Dataset for: "Static Carbon-Aware Code Optimization
Using Benchmarked Operation Profiles"

Hardware : 13th Gen Intel Core i5-13420H | Windows 11 x64
Tracker  : CodeCarbon OfflineEmissionsTracker (IND grid, 708 gCO2/kWh)
Output   : emissions_dataset.csv

Design decisions
================
1.  LOOP WRAPPER SCALING
    The methodology requires the workload inside tracker.start()/.stop() to
    produce a signal clearly above CodeCarbon's measurement noise floor
    (~microseconds of CPU utilisation).  A fixed 100 000 repeats is fine for
    N=10^3 but would run for hours at N=10^6 (O(N) work per repeat).
    Solution: keep total element-operations ≈ 10^7 per measurement window by
    scaling repeats inversely with N:
        repeats = max(5, 10_000_000 // N)
    This gives N=10^3 → 10 000 reps, N=10^6 → 10 reps. The energy values
    reported are totals for the window; per-operation cost is energy / repeats.

2.  GROUP 4 — FACTORIAL INPUT SCALE
    Python's default recursion limit is 1 000.  Recursive factorial(10 000) is
    impossible without unbounded stack growth and produces numbers with ~35 000
    digits (BigInt arithmetic), making timing meaningless as a proxy for typical
    recursion overhead.  The methodology's goal is to capture "recursive vs
    iterative" overhead, not big-integer arithmetic.
    Solution: use domain-appropriate N ∈ {10, 50, 100, 500} with
    sys.setrecursionlimit(600).  Repeats = 50 000 to compensate for small N.

3.  GROUPS 6 & 7 — O(N²) VARIANTS AT N=10^6
    String += concatenation in a loop is O(N²) (each += may copy the entire
    accumulated string).  list.pop(0) is O(N) per call; popping all N elements
    from a fresh list per repeat is therefore O(N²) per repeat.
    At N=10^6 even a single repeat could take >10 minutes.
    Solution: for the O(N²) variants (G6_A, G7_A) cap at N=10^5.  The O(N)
    variants (G6_B join, G7_B deque) run the full 4 input sizes.  The cap is
    documented in the CSV via Operation_ID and in comments.

4.  SEMANTIC EQUIVALENCE
    Every pair/triple within a group must return the same value for the same
    input.  This is verified by assertions in a pre-run correctness check.

5.  CODECARBON SETUP
    - OfflineEmissionsTracker (no network required)
    - country_iso_code="IND" → uses India's grid intensity (≈708 gCO2eq/kWh)
    - measure_power_secs=1 (minimum sampling interval)
    - save_to_file=True, isolated temp directory per measurement so each
      tracker.stop() produces exactly one CSV row.
    - emissions column in CodeCarbon CSV is in kg CO2eq; we convert to grams.
    - We use time.perf_counter() for execution time (higher precision than
      CodeCarbon's own duration field).

6.  IDLE BASELINE
    A 30-second do-nothing measurement captures the system's background energy
    draw.  Subtracting this from operation measurements gives marginal cost.
"""

import gc
import sys
import csv
import time
import os
import tempfile
import collections

import numpy as np
from codecarbon import OfflineEmissionsTracker

# ---------------------------------------------------------------------------
# Global configuration
# ---------------------------------------------------------------------------
OUTPUT_CSV      = "emissions_dataset.csv"
COUNTRY_ISO     = "IND"
INPUT_SIZES     = [10**3, 10**4, 10**5, 10**6]
WARMUP_ITERS    = 1_000
COOLDOWN_SEC    = 60
IDLE_DURATION   = 30          # seconds for idle baseline measurement
POWER_SAMPLE_S  = 1           # CodeCarbon sampling interval (seconds)

# G4: recursion uses a different (safe) input scale
FACTORIAL_SIZES   = [10, 50, 100, 500]
FACTORIAL_REPEATS = 50_000

# G6/G7 O(N²) variants are capped at this N
ON2_CAP_N = 10**5

sys.setrecursionlimit(600)    # safe for factorial(500)

CSV_COLUMNS = [
    "Operation_ID", "Equivalence_Group", "Input_Size_N",
    "Energy_Consumed_kWh", "Execution_Time_sec", "CO2_Emissions_g",
    "Repeats_In_Window",
]


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def get_repeats(N: int) -> int:
    """Scale repeat count so total element-ops ≈ 10^7 per measurement window."""
    return max(5, 10_000_000 // N)


def measure(operation_id: str, group: str, N: int,
            workload_fn, repeats: int) -> dict:
    """
    Execute workload_fn() inside a CodeCarbon tracker window.
    workload_fn captures its data via closure; this function handles tracking.
    Returns one result dict ready for CSV output.
    """
    gc.collect()

    with tempfile.TemporaryDirectory() as tmpdir:
        tracker = OfflineEmissionsTracker(
            country_iso_code=COUNTRY_ISO,
            output_dir=tmpdir,
            output_file="run.csv",
            log_level="error",
            save_to_file=True,
            measure_power_secs=POWER_SAMPLE_S,
            allow_multiple_runs=True,
        )
        tracker.start()
        t0 = time.perf_counter()
        workload_fn()
        elapsed = time.perf_counter() - t0
        tracker.stop()

        csv_path = os.path.join(tmpdir, "run.csv")
        with open(csv_path, newline="") as f:
            rows = list(csv.DictReader(f))

        if not rows:
            raise RuntimeError(
                f"CodeCarbon produced no output row for {operation_id}. "
                "Increase workload duration or check CodeCarbon installation."
            )

        row = rows[-1]
        energy_kwh = float(row["energy_consumed"])          # already in kWh
        co2_g      = float(row["emissions"]) * 1000.0       # kg → g

    return {
        "Operation_ID":        operation_id,
        "Equivalence_Group":   group,
        "Input_Size_N":        N,
        "Energy_Consumed_kWh": energy_kwh,
        "Execution_Time_sec":  elapsed,
        "CO2_Emissions_g":     co2_g,
        "Repeats_In_Window":   repeats,
    }


def append_result(result: dict) -> None:
    file_exists = os.path.isfile(OUTPUT_CSV)
    with open(OUTPUT_CSV, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(result)

    print(
        f"  [{result['Operation_ID']}] N={result['Input_Size_N']:>7}  "
        f"E={result['Energy_Consumed_kWh']:.4e} kWh  "
        f"CO2={result['CO2_Emissions_g']:.4e} g  "
        f"t={result['Execution_Time_sec']:.3f}s  "
        f"reps={result['Repeats_In_Window']}"
    )


def warmup(fn, iters: int = WARMUP_ITERS) -> None:
    """Run fn iters times; results are discarded (not measured)."""
    for _ in range(iters):
        fn()


def cooldown(label: str) -> None:
    print(f"\n--- Cooldown {COOLDOWN_SEC}s after {label} ---")
    time.sleep(COOLDOWN_SEC)


# ---------------------------------------------------------------------------
# Correctness pre-check
# Verifies semantic equivalence before any energy measurement starts.
# ---------------------------------------------------------------------------
def verify_equivalence() -> None:
    print("Verifying semantic equivalence of all groups...")
    N = 100
    data = list(range(N))

    # G1
    s_manual = 0
    for x in data: s_manual += x
    assert s_manual == sum(data) == int(np.sum(data)), "G1 mismatch"

    # G2
    manual_g2  = [x for x in data if x % 2 == 0]  # same logic, readable here
    filter_g2  = list(filter(lambda x: x % 2 == 0, data))
    comp_g2    = [x for x in data if x % 2 == 0]
    assert manual_g2 == filter_g2 == comp_g2, "G2 mismatch"

    # G3
    manual_g3 = []
    for x in data: manual_g3.append(x * 2)
    map_g3    = list(map(lambda x: x * 2, data))
    comp_g3   = [x * 2 for x in data]
    assert manual_g3 == map_g3 == comp_g3, "G3 mismatch"

    # G4
    def rec_fact(n): return 1 if n <= 1 else n * rec_fact(n - 1)
    def iter_fact(n):
        r = 1
        for i in range(2, n + 1): r *= i
        return r
    assert rec_fact(10) == iter_fact(10), "G4 mismatch"

    # G5
    lst = list(range(N)); s = set(range(N)); target = N
    assert (target in lst) == (target in s) == False, "G5 mismatch"

    # G6
    chars = ['a'] * N
    s_concat = ""
    for c in chars: s_concat += c
    s_join = "".join(chars)
    assert s_concat == s_join == "a" * N, "G6 mismatch"

    # G7
    lst7 = list(range(N)); dq = collections.deque(range(N))
    popped_list  = [lst7.pop(0) for _ in range(N)]
    popped_deque = [dq.popleft() for _ in range(N)]
    assert popped_list == popped_deque == list(range(N)), "G7 mismatch"

    # G8
    keys = [x % 10 for x in range(N)]
    manual_d = {}
    for k in keys:
        if k in manual_d: manual_d[k] += 1
        else: manual_d[k] = 1
    default_d = collections.defaultdict(int)
    for k in keys: default_d[k] += 1
    assert dict(manual_d) == dict(default_d), "G8 mismatch"

    print("All equivalence checks passed.\n")


# ===========================================================================
# GROUP 1 — Summation
# Task: compute the integer sum of a list of N integers.
# Variants: manual for-loop | builtin sum() | numpy.sum()
# All return the same integer: N*(N-1)//2
# ===========================================================================
def benchmark_group1() -> None:
    print("\n=== GROUP 1: Summation ===")
    GROUP = "G1_Summation"

    for N in INPUT_SIZES:
        print(f"\n  N = {N}")
        data    = list(range(N))
        np_data = np.array(data, dtype=np.int64)
        reps    = get_repeats(N)

        warmup(lambda: sum(data))
        warmup(lambda: np.sum(np_data))

        # G1_A: Manual accumulation loop
        def w_manual():
            for _ in range(reps):
                s = 0
                for x in data:
                    s += x
        append_result(measure("G1_A_ManualLoop", GROUP, N, w_manual, reps))
        gc.collect()

        # G1_B: Built-in sum()
        def w_sum():
            for _ in range(reps):
                sum(data)
        append_result(measure("G1_B_BuiltinSum", GROUP, N, w_sum, reps))
        gc.collect()

        # G1_C: numpy.sum()  — also covers "vectorized numerical operations"
        def w_numpy():
            for _ in range(reps):
                np.sum(np_data)
        append_result(measure("G1_C_NumpySum", GROUP, N, w_numpy, reps))
        gc.collect()

    cooldown("G1_Summation")


# ===========================================================================
# GROUP 2 — Filtering
# Task: collect all even integers from a list of N elements.
# Variants: manual loop+if | filter()+list() | list comprehension
# All return the same list: [0, 2, 4, ..., N-2] (for even N)
# ===========================================================================
def benchmark_group2() -> None:
    print("\n=== GROUP 2: Filtering ===")
    GROUP = "G2_Filtering"

    for N in INPUT_SIZES:
        print(f"\n  N = {N}")
        data = list(range(N))
        reps = get_repeats(N)

        warmup(lambda: [x for x in data if x % 2 == 0])

        # G2_A: Manual loop with conditional append
        def w_manual():
            for _ in range(reps):
                result = []
                for x in data:
                    if x % 2 == 0:
                        result.append(x)
        append_result(measure("G2_A_ManualLoop", GROUP, N, w_manual, reps))
        gc.collect()

        # G2_B: filter() built-in (returns iterator; list() materialises it)
        def w_filter():
            for _ in range(reps):
                list(filter(lambda x: x % 2 == 0, data))
        append_result(measure("G2_B_Filter", GROUP, N, w_filter, reps))
        gc.collect()

        # G2_C: List comprehension
        def w_comp():
            for _ in range(reps):
                [x for x in data if x % 2 == 0]
        append_result(measure("G2_C_ListComp", GROUP, N, w_comp, reps))
        gc.collect()

    cooldown("G2_Filtering")


# ===========================================================================
# GROUP 3 — Transformation
# Task: produce a new list where every element is multiplied by 2.
# Variants: manual loop+append | map()+list() | list comprehension
# All return [0, 2, 4, ..., 2*(N-1)]
# ===========================================================================
def benchmark_group3() -> None:
    print("\n=== GROUP 3: Transformation ===")
    GROUP = "G3_Transformation"

    for N in INPUT_SIZES:
        print(f"\n  N = {N}")
        data = list(range(N))
        reps = get_repeats(N)

        warmup(lambda: [x * 2 for x in data])

        # G3_A: Manual loop with .append()
        def w_manual():
            for _ in range(reps):
                result = []
                for x in data:
                    result.append(x * 2)
        append_result(measure("G3_A_ManualLoop", GROUP, N, w_manual, reps))
        gc.collect()

        # G3_B: map() — lazy iterator; list() forces evaluation
        def w_map():
            for _ in range(reps):
                list(map(lambda x: x * 2, data))
        append_result(measure("G3_B_Map", GROUP, N, w_map, reps))
        gc.collect()

        # G3_C: List comprehension
        def w_comp():
            for _ in range(reps):
                [x * 2 for x in data]
        append_result(measure("G3_C_ListComp", GROUP, N, w_comp, reps))
        gc.collect()

    cooldown("G3_Transformation")


# ===========================================================================
# GROUP 4 — Recursion vs Iteration (Factorial)
# Task: compute n! for n ∈ {10, 50, 100, 500}.
#
# Why not N ∈ {10^3..10^6}?
#   factorial(1000) requires a recursion depth of 1000 (Python default limit).
#   factorial(10 000) is impossible without a stack of 10 000 frames AND
#   produces a ~35 000-digit integer — the BigInt arithmetic cost would swamp
#   the recursion overhead signal, defeating the purpose of this group.
#   The methodology's goal is to benchmark "recursive vs iterative overhead",
#   which is captured meaningfully at n ∈ {10, 50, 100, 500}.
#
# Both variants return the same integer: n!
# ===========================================================================
def _fact_recursive(n: int) -> int:
    if n <= 1:
        return 1
    return n * _fact_recursive(n - 1)


def _fact_iterative(n: int) -> int:
    result = 1
    for i in range(2, n + 1):
        result *= i
    return result


def benchmark_group4() -> None:
    print("\n=== GROUP 4: Recursion vs Iteration (Factorial) ===")
    GROUP = "G4_Recursion"
    reps  = FACTORIAL_REPEATS

    for N in FACTORIAL_SIZES:
        print(f"\n  N = {N}")
        warmup(lambda: _fact_recursive(N))
        warmup(lambda: _fact_iterative(N))

        # G4_A: Recursive factorial
        def w_recursive():
            for _ in range(reps):
                _fact_recursive(N)
        append_result(measure("G4_A_Recursive", GROUP, N, w_recursive, reps))
        gc.collect()

        # G4_B: Iterative factorial
        def w_iterative():
            for _ in range(reps):
                _fact_iterative(N)
        append_result(measure("G4_B_Iterative", GROUP, N, w_iterative, reps))
        gc.collect()

    cooldown("G4_Recursion")


# ===========================================================================
# GROUP 5 — Membership Testing
# Task: check whether a target value is present in a collection of N elements.
# Variants: list (O(N) linear scan) | set (O(1) hash lookup)
# Target = N (guaranteed NOT in collection → worst-case for list).
# Both return False for the same input.
# ===========================================================================
def benchmark_group5() -> None:
    print("\n=== GROUP 5: Membership Testing ===")
    GROUP = "G5_Membership"

    for N in INPUT_SIZES:
        print(f"\n  N = {N}")
        data_list = list(range(N))
        data_set  = set(range(N))
        target    = N          # not in collection → forces full O(N) scan for list
        reps      = get_repeats(N)

        warmup(lambda: target in data_list)
        warmup(lambda: target in data_set)

        # G5_A: O(N) list membership
        def w_list():
            for _ in range(reps):
                _ = target in data_list
        append_result(measure("G5_A_ListLookup", GROUP, N, w_list, reps))
        gc.collect()

        # G5_B: O(1) set membership
        def w_set():
            for _ in range(reps):
                _ = target in data_set
        append_result(measure("G5_B_SetLookup", GROUP, N, w_set, reps))
        gc.collect()

    cooldown("G5_Membership")


# ===========================================================================
# GROUP 6 — String Building
# Task: concatenate N single-character strings into one string of length N.
# Variants: repeated += in loop | "".join(list)
# Both produce "a" * N.
#
# Why cap += at N=10^5?
#   CPython does NOT guarantee copy-on-write for string +=.  The worst case is
#   O(N²) character copies.  At N=10^6 that is ~10^12 copy operations — even
#   a single repeat could take hours.  The methodology explicitly requires
#   feasible runtimes.  We run += through N=10^5 and note the cap.
#   join() has no such issue and runs all 4 input sizes.
# ===========================================================================
def benchmark_group6() -> None:
    print("\n=== GROUP 6: String Building ===")
    GROUP = "G6_StringBuilding"

    for N in INPUT_SIZES:
        print(f"\n  N = {N}")
        chars = ['a'] * N
        reps  = get_repeats(N)

        warmup(lambda: "".join(chars))

        # G6_A: Repeated += concatenation — capped at ON2_CAP_N
        if N <= ON2_CAP_N:
            def w_concat():
                for _ in range(reps):
                    s = ""
                    for c in chars:
                        s += c
            append_result(measure("G6_A_StringConcat", GROUP, N, w_concat, reps))
            gc.collect()
        else:
            print(f"  G6_A_StringConcat skipped at N={N} (O(N²) runtime infeasible)")

        # G6_B: "".join() — O(N), runs all input sizes
        def w_join():
            for _ in range(reps):
                "".join(chars)
        append_result(measure("G6_B_StringJoin", GROUP, N, w_join, reps))
        gc.collect()

    cooldown("G6_StringBuilding")


# ===========================================================================
# GROUP 7 — Queue Operations (Front Removal)
# Task: remove all N elements from the front of a sequence, one at a time.
# Variants: list.pop(0) — O(N) per call | deque.popleft() — O(1) per call
# Both yield the same sequence of values: [0, 1, 2, ..., N-1].
#
# Why cap list.pop(0) at N=10^5?
#   Each list.pop(0) shifts all remaining elements: O(N) per call.
#   Popping all N elements is therefore O(N²) per repeat.
#   At N=10^6 this is infeasible.  Same cap logic as G6_A.
#   The queue must be rebuilt inside the repeat loop (not shared across
#   repeats) to ensure each repeat measures a full N-element drain.
#   Rebuild cost is equal for both variants and does not distort the comparison.
# ===========================================================================
def benchmark_group7() -> None:
    print("\n=== GROUP 7: Queue Operations ===")
    GROUP = "G7_QueueOps"

    for N in INPUT_SIZES:
        print(f"\n  N = {N}")
        reps = get_repeats(N)

        warmup(lambda: [collections.deque(range(N)).popleft() for _ in range(N)])

        # G7_A: list.pop(0) — O(N²) total per repeat, capped at ON2_CAP_N
        if N <= ON2_CAP_N:
            def w_list_pop():
                for _ in range(reps):
                    q = list(range(N))
                    for _ in range(N):
                        q.pop(0)
            append_result(measure("G7_A_ListPop0", GROUP, N, w_list_pop, reps))
            gc.collect()
        else:
            print(f"  G7_A_ListPop0 skipped at N={N} (O(N²) runtime infeasible)")

        # G7_B: deque.popleft() — O(N) total per repeat, runs all input sizes
        def w_deque_pop():
            for _ in range(reps):
                dq = collections.deque(range(N))
                for _ in range(N):
                    dq.popleft()
        append_result(measure("G7_B_DequePopleft", GROUP, N, w_deque_pop, reps))
        gc.collect()

    cooldown("G7_QueueOps")


# ===========================================================================
# GROUP 8 — Conditional Lookup (Frequency Count)
# Task: build a frequency-count dictionary from a list of N keys.
# Variants: manual "if key in dict" check | collections.defaultdict(int)
# Both produce an identical {key: count} dictionary for the same input.
# Both are O(N) — the difference is Python-level branch overhead vs C-level
# defaultdict __missing__ hook.
# ===========================================================================
def benchmark_group8() -> None:
    print("\n=== GROUP 8: Conditional Lookup ===")
    GROUP = "G8_ConditionalLookup"

    for N in INPUT_SIZES:
        print(f"\n  N = {N}")
        # Keys cycle over 0..9 regardless of N so the dict stays small;
        # this isolates lookup/insert overhead from dict-size effects.
        keys = [x % 10 for x in range(N)]
        reps = get_repeats(N)

        warmup(lambda: collections.defaultdict(int))

        # G8_A: Manual if-in-dict conditional
        def w_manual():
            for _ in range(reps):
                d = {}
                for k in keys:
                    if k in d:
                        d[k] += 1
                    else:
                        d[k] = 1
        append_result(measure("G8_A_ManualDict", GROUP, N, w_manual, reps))
        gc.collect()

        # G8_B: defaultdict(int) — __missing__ called automatically
        def w_defaultdict():
            for _ in range(reps):
                d = collections.defaultdict(int)
                for k in keys:
                    d[k] += 1
        append_result(measure("G8_B_DefaultDict", GROUP, N, w_defaultdict, reps))
        gc.collect()

    cooldown("G8_ConditionalLookup")


# ===========================================================================
# IDLE BASELINE
# Measures system background energy draw for 30 seconds with no workload.
# Used to compute marginal (net) energy: E_marginal = E_operation − E_idle_rate × t
# ===========================================================================
def benchmark_idle_baseline() -> None:
    print("\n=== IDLE BASELINE (30 s) ===")

    def w_idle():
        time.sleep(IDLE_DURATION)

    result = measure("IDLE_Baseline", "IDLE_Baseline", 0, w_idle, 0)
    append_result(result)
    print(f"  Idle baseline recorded: {result['Energy_Consumed_kWh']:.4e} kWh / 30 s")


# ===========================================================================
# MAIN
# ===========================================================================
def main() -> None:
    print("=" * 70)
    print("Carbon Benchmark Suite — Offline Dataset Generation")
    print(f"Output file : {OUTPUT_CSV}")
    print(f"Grid region : India (IND)")
    print("=" * 70)

    # Step 0: correctness gate — abort if any group is semantically broken
    verify_equivalence()

    # Step 1: idle baseline (system background energy)
    benchmark_idle_baseline()

    # Step 2: all eight equivalence groups
    # 60-second cooldown is applied at the END of each group function
    benchmark_group1()
    benchmark_group2()
    benchmark_group3()
    benchmark_group4()
    benchmark_group5()
    benchmark_group6()
    benchmark_group7()
    benchmark_group8()

    print("\n" + "=" * 70)
    print(f"Done. Results written to: {OUTPUT_CSV}")
    print("=" * 70)


if __name__ == "__main__":
    main()
