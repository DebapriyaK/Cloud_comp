"""
benchmark_suite.py
------------------
Offline / Online Benchmark Dataset for:
"Static Carbon-Aware Code Optimization Using Benchmarked Operation Profiles"

Hardware : 13th Gen Intel Core i5-13420H | Windows 11 x64
Tracker  : CodeCarbon OfflineEmissionsTracker (IND grid, 708 gCO2/kWh)  [--mode offline]
           CodeCarbon EmissionsTracker (live grid data)                   [--mode online]
Output   : emissions_dataset.csv

HOW TO RUN
==========
Offline mode (default, no internet needed):
    python benchmark_suite.py --mode offline

Online mode (needs internet — fetches live India grid intensity):
    python benchmark_suite.py --mode online

The Run_Mode column in the CSV tells you which rows came from which run.
Run BOTH modes and keep all rows — carbon_analyzer.py will use them.

NEW IN THIS VERSION
===================
1. --mode argument  : switch between offline and online CodeCarbon tracker
2. Run_Mode column  : tags every CSV row as "offline" or "online"
3. GROUP 9          : benchmarks if-else vs while vs do-while loop constructs

Design decisions (carried over from original)
=============================================
1.  LOOP WRAPPER SCALING
    repeats = max(5, 10_000_000 // N)
    Keeps total element-operations ≈ 10^7 per measurement window.

2.  GROUP 4 — FACTORIAL INPUT SCALE
    Python default recursion limit is 1 000. Use N in {10,50,100,500}
    with sys.setrecursionlimit(600). Repeats = 50 000 to compensate.

3.  GROUPS 6 & 7 — O(N²) VARIANTS AT N=10^6
    String += and list.pop(0) are O(N²). Capped at N=10^5.

4.  SEMANTIC EQUIVALENCE
    Every pair/triple within a group returns the same value for same input.
    Verified by assertions before measurement starts.

5.  CODECARBON SETUP
    - OfflineEmissionsTracker: country_iso_code="IND" (708 gCO2eq/kWh fixed)
    - EmissionsTracker (online): fetches real-time grid intensity for India
    - measure_power_secs=1, save_to_file=True, isolated temp dir per run
    - emissions column in CodeCarbon CSV is kg CO2eq; we convert to grams
    - time.perf_counter() for execution time (higher precision)

6.  IDLE BASELINE
    30-second do-nothing measurement captures background energy draw.

7.  GROUP 9 — LOOP CONSTRUCTS
    Task: sum integers 0..N-1 using three control-flow patterns.
    G9_A: for-loop with if-else branch on every element
    G9_B: while loop with manual index
    G9_C: do-while simulation (while True + break at end)
    All produce the same integer sum: N*(N-1)//2
    This captures branch-prediction overhead vs plain loop overhead.
"""

import argparse
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
# Parse command-line mode argument
# ---------------------------------------------------------------------------
parser = argparse.ArgumentParser(
    description="Carbon benchmark suite — offline or online tracker mode."
)
parser.add_argument(
    "--mode",
    choices=["offline", "online"],
    default="offline",
    help="offline = OfflineEmissionsTracker (fixed IND grid 708 gCO2/kWh)\n"
         "online  = EmissionsTracker (fetches live grid intensity)",
)
args = parser.parse_args()
RUN_MODE = args.mode   # "offline" or "online"

# ---------------------------------------------------------------------------
# Global configuration
# ---------------------------------------------------------------------------
OUTPUT_CSV      = "emissions_dataset.csv"
COUNTRY_ISO     = "IND"
INPUT_SIZES     = [10**3, 10**4, 10**5, 10**6]
WARMUP_ITERS    = 1_000
COOLDOWN_SEC    = 60
IDLE_DURATION   = 30          # seconds for idle baseline
POWER_SAMPLE_S  = 1           # CodeCarbon sampling interval (seconds)

# G4: safe input scale for recursion
FACTORIAL_SIZES   = [10, 50, 100, 500]
FACTORIAL_REPEATS = 50_000

# G6/G7 O(N²) variants capped here
ON2_CAP_N = 10**5

sys.setrecursionlimit(600)    # safe for factorial(500)

# CSV now has Run_Mode column so offline and online rows can coexist
CSV_COLUMNS = [
    "Operation_ID", "Equivalence_Group", "Input_Size_N",
    "Energy_Consumed_kWh", "Execution_Time_sec", "CO2_Emissions_g",
    "Repeats_In_Window", "Run_Mode",
]


# ---------------------------------------------------------------------------
# Tracker factory — returns the right CodeCarbon tracker based on --mode
# ---------------------------------------------------------------------------
def make_tracker(output_dir: str):
    """
    Return a CodeCarbon tracker configured for the current RUN_MODE.

    offline → OfflineEmissionsTracker  (no network, fixed IND 708 gCO2/kWh)
    online  → EmissionsTracker         (fetches live Indian grid intensity)
    """
    common = dict(
        output_dir=output_dir,
        output_file="run.csv",
        log_level="error",
        save_to_file=True,
        measure_power_secs=POWER_SAMPLE_S,
        allow_multiple_runs=True,
    )
    if RUN_MODE == "offline":
        return OfflineEmissionsTracker(country_iso_code=COUNTRY_ISO, **common)
    else:
        # Online tracker: import here so offline-only installs still work
        from codecarbon import EmissionsTracker
        return EmissionsTracker(country_iso_code=COUNTRY_ISO, **common)


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
    Returns one result dict ready for CSV output.
    Includes Run_Mode so offline and online rows are distinguishable.
    """
    gc.collect()

    with tempfile.TemporaryDirectory() as tmpdir:
        tracker = make_tracker(tmpdir)
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
        energy_kwh = float(row["energy_consumed"])
        co2_g      = float(row["emissions"]) * 1000.0   # kg → g

    return {
        "Operation_ID":        operation_id,
        "Equivalence_Group":   group,
        "Input_Size_N":        N,
        "Energy_Consumed_kWh": energy_kwh,
        "Execution_Time_sec":  elapsed,
        "CO2_Emissions_g":     co2_g,
        "Repeats_In_Window":   repeats,
        "Run_Mode":            RUN_MODE,
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
        f"reps={result['Repeats_In_Window']}  "
        f"mode={result['Run_Mode']}"
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
# Verifies semantic equivalence of all groups before energy measurement.
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
    manual_g2 = [x for x in data if x % 2 == 0]
    filter_g2 = list(filter(lambda x: x % 2 == 0, data))
    comp_g2   = [x for x in data if x % 2 == 0]
    assert manual_g2 == filter_g2 == comp_g2, "G2 mismatch"

    # G3
    manual_g3 = []
    for x in data: manual_g3.append(x * 2)
    map_g3  = list(map(lambda x: x * 2, data))
    comp_g3 = [x * 2 for x in data]
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

    # G9 — verify all three loop constructs produce the same sum
    expected_sum = N * (N - 1) // 2

    # G9_A: for-loop with if-else (adds x regardless of branch — same result)
    s_ifelse = 0
    for x in data:
        if x % 2 == 0:
            s_ifelse += x
        else:
            s_ifelse += x
    assert s_ifelse == expected_sum, "G9_A mismatch"

    # G9_B: while loop
    s_while = 0
    i = 0
    while i < N:
        s_while += data[i]
        i += 1
    assert s_while == expected_sum, "G9_B mismatch"

    # G9_C: do-while simulation
    s_dowhile = 0
    i = 0
    while True:
        s_dowhile += data[i]
        i += 1
        if i >= N:
            break
    assert s_dowhile == expected_sum, "G9_C mismatch"

    print("All equivalence checks passed.\n")


# ===========================================================================
# GROUP 1 — Summation
# Task: compute the integer sum of a list of N integers.
# Variants: manual for-loop | builtin sum() | numpy.sum()
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

        def w_manual():
            for _ in range(reps):
                s = 0
                for x in data:
                    s += x
        append_result(measure("G1_A_ManualLoop", GROUP, N, w_manual, reps))
        gc.collect()

        def w_sum():
            for _ in range(reps):
                sum(data)
        append_result(measure("G1_B_BuiltinSum", GROUP, N, w_sum, reps))
        gc.collect()

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
# ===========================================================================
def benchmark_group2() -> None:
    print("\n=== GROUP 2: Filtering ===")
    GROUP = "G2_Filtering"

    for N in INPUT_SIZES:
        print(f"\n  N = {N}")
        data = list(range(N))
        reps = get_repeats(N)

        warmup(lambda: [x for x in data if x % 2 == 0])

        def w_manual():
            for _ in range(reps):
                result = []
                for x in data:
                    if x % 2 == 0:
                        result.append(x)
        append_result(measure("G2_A_ManualLoop", GROUP, N, w_manual, reps))
        gc.collect()

        def w_filter():
            for _ in range(reps):
                list(filter(lambda x: x % 2 == 0, data))
        append_result(measure("G2_B_Filter", GROUP, N, w_filter, reps))
        gc.collect()

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
# ===========================================================================
def benchmark_group3() -> None:
    print("\n=== GROUP 3: Transformation ===")
    GROUP = "G3_Transformation"

    for N in INPUT_SIZES:
        print(f"\n  N = {N}")
        data = list(range(N))
        reps = get_repeats(N)

        warmup(lambda: [x * 2 for x in data])

        def w_manual():
            for _ in range(reps):
                result = []
                for x in data:
                    result.append(x * 2)
        append_result(measure("G3_A_ManualLoop", GROUP, N, w_manual, reps))
        gc.collect()

        def w_map():
            for _ in range(reps):
                list(map(lambda x: x * 2, data))
        append_result(measure("G3_B_Map", GROUP, N, w_map, reps))
        gc.collect()

        def w_comp():
            for _ in range(reps):
                [x * 2 for x in data]
        append_result(measure("G3_C_ListComp", GROUP, N, w_comp, reps))
        gc.collect()

    cooldown("G3_Transformation")


# ===========================================================================
# GROUP 4 — Recursion vs Iteration (Factorial)
# Task: compute n! for n in {10, 50, 100, 500}.
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

        def w_recursive():
            for _ in range(reps):
                _fact_recursive(N)
        append_result(measure("G4_A_Recursive", GROUP, N, w_recursive, reps))
        gc.collect()

        def w_iterative():
            for _ in range(reps):
                _fact_iterative(N)
        append_result(measure("G4_B_Iterative", GROUP, N, w_iterative, reps))
        gc.collect()

    cooldown("G4_Recursion")


# ===========================================================================
# GROUP 5 — Membership Testing
# Task: check whether target is present in collection of N elements.
# Variants: list (O(N)) | set (O(1))
# ===========================================================================
def benchmark_group5() -> None:
    print("\n=== GROUP 5: Membership Testing ===")
    GROUP = "G5_Membership"

    for N in INPUT_SIZES:
        print(f"\n  N = {N}")
        data_list = list(range(N))
        data_set  = set(range(N))
        target    = N
        reps      = get_repeats(N)

        warmup(lambda: target in data_list)
        warmup(lambda: target in data_set)

        def w_list():
            for _ in range(reps):
                _ = target in data_list
        append_result(measure("G5_A_ListLookup", GROUP, N, w_list, reps))
        gc.collect()

        def w_set():
            for _ in range(reps):
                _ = target in data_set
        append_result(measure("G5_B_SetLookup", GROUP, N, w_set, reps))
        gc.collect()

    cooldown("G5_Membership")


# ===========================================================================
# GROUP 6 — String Building
# Task: concatenate N single-character strings into one string of length N.
# Variants: repeated += | "".join()
# += variant capped at N=10^5 (O(N²) risk)
# ===========================================================================
def benchmark_group6() -> None:
    print("\n=== GROUP 6: String Building ===")
    GROUP = "G6_StringBuilding"

    for N in INPUT_SIZES:
        print(f"\n  N = {N}")
        chars = ['a'] * N
        reps  = get_repeats(N)

        warmup(lambda: "".join(chars))

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

        def w_join():
            for _ in range(reps):
                "".join(chars)
        append_result(measure("G6_B_StringJoin", GROUP, N, w_join, reps))
        gc.collect()

    cooldown("G6_StringBuilding")


# ===========================================================================
# GROUP 7 — Queue Operations (Front Removal)
# Task: remove all N elements from the front of a sequence.
# Variants: list.pop(0) (O(N²)) | deque.popleft() (O(N))
# list.pop(0) capped at N=10^5
# ===========================================================================
def benchmark_group7() -> None:
    print("\n=== GROUP 7: Queue Operations ===")
    GROUP = "G7_QueueOps"

    for N in INPUT_SIZES:
        print(f"\n  N = {N}")
        reps = get_repeats(N)

        warmup(lambda: [collections.deque(range(N)).popleft() for _ in range(N)])

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
# Variants: manual if-in-dict | collections.defaultdict(int)
# ===========================================================================
def benchmark_group8() -> None:
    print("\n=== GROUP 8: Conditional Lookup ===")
    GROUP = "G8_ConditionalLookup"

    for N in INPUT_SIZES:
        print(f"\n  N = {N}")
        keys = [x % 10 for x in range(N)]
        reps = get_repeats(N)

        warmup(lambda: collections.defaultdict(int))

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

        def w_defaultdict():
            for _ in range(reps):
                d = collections.defaultdict(int)
                for k in keys:
                    d[k] += 1
        append_result(measure("G8_B_DefaultDict", GROUP, N, w_defaultdict, reps))
        gc.collect()

    cooldown("G8_ConditionalLookup")


# ===========================================================================
# GROUP 9 — Loop Constructs  *** NEW ***
#
# Task: sum all integers 0..N-1 using three different loop/branch patterns.
# All three produce the same integer: N*(N-1)//2
#
# G9_A  for-loop with if-else branch on every iteration
#       Models code where every element goes through a condition check.
#       The if and else arms do the same work — this isolates the overhead
#       of the branch itself (branch-prediction cost).
#
# G9_B  while loop with manual index variable
#       Classic C-style counted loop. Python must update i and compare
#       on every iteration — slightly more bytecode than a for-loop.
#
# G9_C  do-while simulation  (Python has no native do-while)
#       Implemented as:
#           while True:
#               body
#               if not condition: break
#       The loop body always executes at least once. The extra "if not"
#       check at the END of each iteration is the key difference vs while.
#
# Why these three?
#   They represent common loop patterns students write and are directly
#   comparable in any language that has all three constructs. Measuring
#   them here gives a carbon-cost profile for loop-style choice.
# ===========================================================================
def benchmark_group9() -> None:
    print("\n=== GROUP 9: Loop Constructs (if-else / while / do-while) ===")
    GROUP = "G9_LoopConstructs"

    for N in INPUT_SIZES:
        print(f"\n  N = {N}")
        data = list(range(N))
        reps = get_repeats(N)

        # Warmup with the fastest variant
        warmup(lambda: sum(data))

        # ------------------------------------------------------------------
        # G9_A: for-loop with if-else branch on every element
        # Both branches add x — same result, but the branch adds overhead.
        # ------------------------------------------------------------------
        def w_ifelse():
            for _ in range(reps):
                s = 0
                for x in data:
                    if x % 2 == 0:
                        s += x      # even branch
                    else:
                        s += x      # odd branch  (same operation)
        append_result(measure("G9_A_IfElse", GROUP, N, w_ifelse, reps))
        gc.collect()

        # ------------------------------------------------------------------
        # G9_B: while loop with manual index variable
        # ------------------------------------------------------------------
        def w_while():
            for _ in range(reps):
                s = 0
                i = 0
                while i < N:
                    s += data[i]
                    i += 1
        append_result(measure("G9_B_While", GROUP, N, w_while, reps))
        gc.collect()

        # ------------------------------------------------------------------
        # G9_C: do-while simulation using while True + break
        # Body runs at least once; condition checked at END of each iteration.
        # ------------------------------------------------------------------
        def w_dowhile():
            for _ in range(reps):
                s = 0
                i = 0
                while True:
                    s += data[i]
                    i += 1
                    if i >= N:      # exit condition checked at end (do-while style)
                        break
        append_result(measure("G9_C_DoWhile", GROUP, N, w_dowhile, reps))
        gc.collect()

    cooldown("G9_LoopConstructs")


# ===========================================================================
# IDLE BASELINE
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
    print("Carbon Benchmark Suite — Dataset Generation")
    print(f"Output file : {OUTPUT_CSV}")
    print(f"Grid region : India (IND)")
    print(f"Tracker mode: {RUN_MODE.upper()}")
    print("=" * 70)
    print()
    print("NOTE: This run will APPEND to emissions_dataset.csv.")
    print("      Run once with --mode offline, once with --mode online.")
    print("      The Run_Mode column distinguishes the two sets of rows.")
    print()

    # Step 0: correctness gate
    verify_equivalence()

    # Step 1: idle baseline
    benchmark_idle_baseline()

    # Step 2: all nine equivalence groups
    benchmark_group1()
    benchmark_group2()
    benchmark_group3()
    benchmark_group4()
    benchmark_group5()
    benchmark_group6()
    benchmark_group7()
    benchmark_group8()
    benchmark_group9()   # NEW: loop constructs

    print("\n" + "=" * 70)
    print(f"Done. Results written to: {OUTPUT_CSV}")
    print(f"Mode: {RUN_MODE}")
    print("=" * 70)


if __name__ == "__main__":
    main()


