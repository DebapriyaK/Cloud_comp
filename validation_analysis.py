"""
validation_analysis.py
----------------------
Reads validation_results.csv and computes:
  - Mean emission per pattern/version (5 runs each)
  - CO2 reduction % for each group
  - Summary table at the end

NOTE: If validation_results.csv contains duplicate run sets
(from running validation_tests.py more than once without deleting
the CSV), only the most recent 5 rows per group are used.
"""
import csv
from collections import defaultdict
from statistics import mean, stdev

FILE = "validation_results.csv"

# ------------------------------------------------------------------
# Load data — keep only the LAST 5 rows per (Pattern, Version)
# to avoid mixing results from different runs
# ------------------------------------------------------------------
raw = defaultdict(list)
with open(FILE) as f:
    for r in csv.DictReader(f):
        key = (r["Pattern"], r["Version"])
        raw[key].append(float(r["Emission_kg"]))

# Take the last 5 measurements only (most recent run)
data = {}
for key, vals in raw.items():
    data[key] = vals[-5:]

# ------------------------------------------------------------------
# Group by pattern
# ------------------------------------------------------------------
patterns = sorted({k[0] for k in data})

print("\n=== VALIDATION ANALYSIS ===\n")

for pattern in patterns:
    versions = sorted([k[1] for k in data if k[0] == pattern])
    print(f"{pattern}")

    avgs = {}
    for ver in versions:
        vals = data[(pattern, ver)]
        avg_val = mean(vals)
        sd = stdev(vals) if len(vals) > 1 else 0.0
        avgs[ver] = avg_val
        print(f"  {ver} avg: {avg_val:.8f}  (std: {sd:.2e})")

    # Reduction
    if len(avgs) == 2:
        v = list(avgs.values())
        k = list(avgs.keys())

        # identify worse and better
        if v[0] >= v[1]:
            worse_k, better_k = k[0], k[1]
        else:
            worse_k, better_k = k[1], k[0]

        worse_v = avgs[worse_k]
        better_v = avgs[better_k]
        reduction = (worse_v - better_v) / worse_v * 100

        if pattern == "G6":
            # G6 difference is tiny — show absolute too
            diff = abs(worse_v - better_v)
            print(f"  Absolute difference: {diff:.2e} kg CO2")

        print(f"  Reduction: {reduction:.2f}%  ({worse_k} → {better_k})")

    print()

# ------------------------------------------------------------------
# Summary table
# ------------------------------------------------------------------
print("=== SUMMARY ===\n")

EXPLANATIONS = {
    "G5": "O(N) list scan → O(1) set lookup  (algorithmic improvement)",
    "G6": "String += copies on each step → join() builds once  (micro-optimisation)",
    "G7": "list.pop(0) shifts N elements → deque.popleft() is O(1)  (algorithmic improvement)",
    "G9": "while loop manual index overhead → for loop  (micro-optimisation)",
}

for pattern in patterns:
    versions = sorted([k[1] for k in data if k[0] == pattern])
    avgs = {ver: mean(data[(pattern, ver)]) for ver in versions}

    if len(avgs) != 2:
        continue

    v = list(avgs.values())
    k = list(avgs.keys())

    if v[0] >= v[1]:
        worse_k, better_k = k[0], k[1]
    else:
        worse_k, better_k = k[1], k[0]

    reduction = (avgs[worse_k] - avgs[better_k]) / avgs[worse_k] * 100
    explanation = EXPLANATIONS.get(pattern, "")
    print(f"  {pattern}: {better_k} is {reduction:.1f}% lower CO2 than {worse_k}")
    print(f"       Reason: {explanation}")
    print()
