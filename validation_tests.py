from codecarbon import EmissionsTracker
import time
import csv
import os
from collections import deque

CSV_FILE = "validation_results.csv"

REPS = 50_000   # FIXED: same rep count for ALL variants in a group
N    = 100_000  # FIXED: same data size for ALL variants


def measure(func, label, pattern, version, run_id):
    tracker = EmissionsTracker(log_level="error")
    tracker.start()

    start = time.perf_counter()
    func()
    end = time.perf_counter()

    emissions = tracker.stop()

    print(f"{label}")
    print(f"Time: {end - start:.4f}s")
    print(f"Emissions: {emissions} kg CO2\n")

    file_exists = os.path.isfile(CSV_FILE)
    with open(CSV_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["Pattern", "Version", "Run", "Emission_kg", "Time_sec"])
        writer.writerow([pattern, version, run_id, emissions, end - start])

    return emissions


# =========================================================
# G5: Membership — list vs set  (FIXED: both use same REPS)
# =========================================================
REPS = 50000

def list_membership():
    N = 100_000
    data = list(range(N))
    target = N + 1

    for _ in range(REPS):
        _ = target in data


def set_membership():
    N = 100_000
    data = set(range(N))
    target = N + 1

    for _ in range(REPS):
        _ = target in data

# =========================================================
# G6: String building — += vs join  (FIXED: same N)
# =========================================================
def string_plus():
    chars = ['a'] * N
    s = ""
    for c in chars:
        s += c


def string_join():
    chars = ['a'] * N
    "".join(chars)


# =========================================================
# G7: Queue — list.pop(0) vs deque.popleft  (already fair)
# =========================================================
def list_queue():
    q = list(range(N))
    while q:
        q.pop(0)


def deque_queue():
    q = deque(range(N))
    while q:
        q.popleft()


# =========================================================
# G9: Loop style (for vs while)
# =========================================================
def for_loop():
    N = 100_000
    total = 0

    for i in range(N):
        total += i


def while_loop():
    N = 100_000
    total = 0
    i = 0

    while i < N:
        total += i
        i += 1


# =========================================================
# RUN EXPERIMENTS
# =========================================================
if __name__ == "__main__":
    RUNS = 5

    print("=== G5: Membership ===")
    for i in range(RUNS):
        measure(list_membership, f"Run {i+1} - List", "G5", "List", i+1)
    for i in range(RUNS):
        measure(set_membership,  f"Run {i+1} - Set",  "G5", "Set",  i+1)

    print("=== G6: String ===")
    for i in range(RUNS):
        measure(string_plus, f"Run {i+1} - +=",   "G6", "+=",   i+1)
    for i in range(RUNS):
        measure(string_join, f"Run {i+1} - join", "G6", "join", i+1)

    print("=== G7: Queue ===")
    for i in range(RUNS):
        measure(list_queue,  f"Run {i+1} - list.pop(0)",   "G7", "list.pop(0)",   i+1)
    for i in range(RUNS):
        measure(deque_queue, f"Run {i+1} - deque.popleft", "G7", "deque.popleft", i+1)

    print("=== G9: Loop Constructs ===")
    for i in range(RUNS):
        measure(for_loop,   f"Run {i+1} - for",   "G9", "for",   i+1)
    for i in range(RUNS):
        measure(while_loop, f"Run {i+1} - while", "G9", "while", i+1)

    print("\n✅ All runs completed. Results saved to validation_results.csv")