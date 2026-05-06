# Carbon-Aware Code Analyzer — Methodology & Limitations

## What the project does
Static analysis of Python source code to detect inefficient patterns
(G5 list membership, G6 string building, G7 queue operations) and
estimate the CO2 cost of those patterns using a pre-measured emissions dataset.

## Dataset design
The benchmark (`benchmark_suite.py`) measures **relative** CO2 cost between
algorithm alternatives using CodeCarbon's OfflineEmissionsTracker with the
Indian grid intensity (708 gCO2eq/kWh). Each row in `emissions_dataset.csv`
represents the total emissions for one "window": `Repeats_In_Window` calls
to the algorithm at `Input_Size_N`.

**Key constraint:** CodeCarbon has a minimum measurement floor of approximately
3×10⁻⁶ kg CO2 per measurement window. Operations faster than ~1 second are
dominated by this instrumentation overhead, not actual CPU power draw.
This means **absolute** emission values for fast operations are unreliable;
only **ratios** between alternatives measured in the same window are meaningful.

## Validation methodology
`validation_tests.py` runs each operation variant 5 times and measures the
full function emission with CodeCarbon. To be a fair comparison:
- All variants in a group use the **same** N (data size) and **same** REPS (iterations)
- Worst-case inputs are used (target not in collection) for membership tests

## Why prediction errors are large
`prediction_vs_actual.py` compares dataset predictions against validation measurements.
Remaining error (~40–95%) stems from:

1. **CodeCarbon floor dominance** — both dataset and validation measurements include the
   ~3×10⁻⁶ kg floor. The actual computation CO2 is much smaller. Ratios are stable;
   absolute values are not.

2. **Hardware/thermal state** — the dataset was benchmarked with 60-second cooldowns
   between groups; validation tests have no cooldown. CPU thermal throttling changes
   effective energy consumption.

3. **Different measurement granularities** — dataset measures 100 repetitions per window;
   validation measures 1 function call per window. The floor is paid differently.

## What the results correctly demonstrate
Despite measurement noise, the **relative ordering** is preserved:
- G5: List membership (O(N)) is ~14× more expensive than Set membership (O(1)) ✅
- G6: String join is faster than += for large N ✅  
- G7: deque.popleft() is ~96% cheaper than list.pop(0) ✅

The static analyzer correctly identifies these patterns in source code and
recommends the more carbon-efficient alternative.

## Known limitations
- G6 at N=100,000 shows join is only marginally faster (both dominated by floor)
- G9 (loop constructs) is flagged by the analyzer and experimentally validated: for-loop is ~1.4% lower CO2 than while-loop (negligible difference, both dominated by CodeCarbon floor)
- The VS Code extension uses heuristic analysis without full type inference



Key Findings & Discussion
1. Major CO₂ savings come from algorithmic improvements

The largest improvements were observed in patterns where algorithmic complexity changes:

G5 (Membership Test):
Switching from list (O(N)) to set (O(1)) resulted in ~98% reduction in CO₂ emissions.
G7 (Queue Operations):
Replacing list.pop(0) (O(N)) with deque.popleft() (O(1)) reduced emissions by ~95%.

👉 These results confirm that data structure choice has the highest impact on energy efficiency.

2. Minor improvements for micro-optimizations
G6 (String building):
join() is slightly more efficient than += (~1–2% improvement)
G9 (Loop constructs):
for vs while shows negligible difference (~1%)

👉 These optimizations are secondary and often overshadowed by measurement noise.

3. Impact of CodeCarbon measurement floor

A key limitation in this project is the minimum measurable emission floor (~3×10⁻⁶ kg CO₂).

This causes:

Small operations to appear similar in emissions
Reduced accuracy in absolute values
High prediction error (up to 90%)

👉 Therefore:

Absolute CO₂ values are unreliable for small operations,
but relative comparisons between alternatives are meaningful.
4. Why prediction error appears high

The prediction vs actual comparison shows large percentage errors because:

CodeCarbon floor dominates measurements
Dataset and validation runs have different repetition scaling
Hardware state (CPU load, thermal throttling) varies

👉 However, the relative ranking remains correct, which is the primary goal.

5. Overall conclusion ("So what?")

This project demonstrates that:

Static code patterns can be mapped to energy-efficient alternatives
Significant carbon savings (90–98%) are achievable through simple changes
Static analysis can guide developers toward greener code without runtime overhead

👉 The analyzer successfully bridges code design → environmental impact
