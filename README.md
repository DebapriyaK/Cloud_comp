# Carbon-Aware Code Analyzer

## Overview
This project analyzes Python code to detect inefficient programming patterns and estimate their CO₂ carbon impact. It combines static code analysis (AST-based), a benchmark-driven emissions dataset, experimental validation using CodeCarbon, and a VS Code extension.

---

## Features
- Detects inefficient patterns (G1–G9) using Python AST
- Suggests optimized alternatives with CO₂ estimates
- Supports `--json` output for the VS Code extension
- Experimental validation with CodeCarbon across 5 runs
- Bar chart visualizations per group
- Automated test suite

---

## Supported Patterns

| Group | Pattern | Dirty | Clean |
|-------|---------|-------|-------|
| G1 | Summation | manual for-loop | `sum()` |
| G2 | Filtering | manual for-loop | list comprehension |
| G3 | Transformation | manual for-loop | list comprehension |
| G5 | Membership | `list` O(N) | `set` O(1) |
| G6 | String building | `+=` in loop | `''.join()` |
| G7 | Queue ops | `list.pop(0)` O(N) | `deque.popleft()` O(1) |
| G9 | Loop construct | `while` loop | `for` loop |

---

## Installation

```bash
pip install codecarbon matplotlib numpy
```

Python 3.9+ required. No other dependencies.

---

## Run Order

Run the scripts in this order:

### Step 1 — Run benchmarks (generates emissions_dataset.csv)
```bash
python benchmark_suite.py --mode offline
```
This runs all 9 benchmark groups across 4 input sizes. Takes ~15 minutes due to 60-second cooldowns. Appends results to `emissions_dataset.csv`.

### Step 2 — Analyze a Python file for inefficiencies
```bash
python carbon_analyzer.py demo_dirty.py
```
Detects patterns in `demo_dirty.py` and prints CO₂ estimates with suggestions.

For JSON output (used by the VS Code extension):
```bash
python carbon_analyzer.py demo_dirty.py --json
```

### Step 3 — Interactive demo
```bash
python demo_interactive.py
```
Live interactive mode: choose pre-loaded scenarios or paste your own code.

### Step 4 — Run validation experiments (generates validation_results.csv)
```bash
python validation_tests.py
```
Measures G5, G6, G7, and G9 variants 5 times each using CodeCarbon. Takes ~15 minutes due to G5/G7 O(N²) runs.

### Step 5 — Analyse validation results
```bash
python validation_analysis.py
```
Prints average emissions per pattern and percentage reduction.

### Step 6 — Compare predicted vs actual
```bash
python prediction_vs_actual.py
```
Compares dataset predictions against validation measurements. Shows error % and explains why errors are large (CodeCarbon measurement floor).

### Step 7 — Generate bar charts
```bash
python plot_benchmark.py
```
Saves one PNG bar chart per group (G1–G9, IDLE) to the current directory.

### Step 8 — Run automated tests
```bash
python tests/run_tests.py
```
Runs 4 test cases checking pattern detection, false positives, and confidence levels.

---

## Output Files

| File | Description |
|------|-------------|
| `emissions_dataset.csv` | Raw benchmark measurements (energy, time, CO₂, reps) |
| `validation_results.csv` | Validation run results (5 runs × 4 groups × 2 variants) |
| `G1_benchmark.png` ... `G9_benchmark.png` | Bar charts per group |
| `METHODOLOGY_NOTES.md` | Full methodology, limitations, and key findings |

---

## Key Results Summary

| Pattern | CO₂ Reduction | Significance |
|---------|--------------|--------------|
| G5: list → set | ~98% | Algorithmic: O(N) → O(1) |
| G7: list.pop(0) → deque | ~95% | Algorithmic: O(N²) → O(N) |
| G6: += → join | ~1–2% | Minor: dominated by measurement floor |
| G9: while → for | ~1–2% | Minor: dominated by measurement floor |

The largest savings come from **data structure choice** (G5, G7), where algorithmic complexity changes. Micro-optimisations (G6, G9) produce marginal gains only visible at large N.

---

## VS Code Extension

The `vscode-extension/` folder contains a VS Code extension. It runs `carbon_analyzer.py --json` on every Python file save and shows inline green decorations for detected patterns.

Install via VS Code: Extensions → Install from VSIX → select `vscode-extension/`.

---

## Methodology & Limitations

See `METHODOLOGY_NOTES.md` for full details on:
- Why CodeCarbon's ~3×10⁻⁶ kg measurement floor dominates fast operations
- Why prediction errors appear large (40–99%) while relative rankings remain correct
- Hardware/thermal differences between benchmark and validation runs