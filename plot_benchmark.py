"""
plot_benchmark.py
-----------------
Generates one bar chart per benchmark group (G1-G9 + IDLE).
Each chart shows average CO2 (g) per operation across all input sizes.
The winning (lowest CO2) bar is highlighted in green; others in steelblue.
X-axis uses short readable labels instead of full operation IDs.
"""
import csv
import matplotlib.pyplot as plt
from collections import defaultdict

FILE = "emissions_dataset.csv"

# ------------------------------------------------------------------
# Short readable labels for each operation
# ------------------------------------------------------------------
SHORT_LABELS = {
    # G1
    "G1_A_ManualLoop":  "Manual\nLoop",
    "G1_B_BuiltinSum":  "Built-in\nsum()",
    "G1_C_NumpySum":    "numpy\nsum()",
    # G2
    "G2_A_ManualLoop":  "Manual\nLoop",
    "G2_B_Filter":      "filter()",
    "G2_C_ListComp":    "List\nComp",
    # G3
    "G3_A_ManualLoop":  "Manual\nLoop",
    "G3_B_Map":         "map()",
    "G3_C_ListComp":    "List\nComp",
    # G4
    "G4_A_Recursive":   "Recursive",
    "G4_B_Iterative":   "Iterative",
    # G5
    "G5_A_ListLookup":  "List\nO(N)",
    "G5_B_SetLookup":   "Set\nO(1)",
    # G6
    "G6_A_StringConcat": "String\n+=",
    "G6_B_StringJoin":   "join()",
    # G7
    "G7_A_ListPop0":    "list\n.pop(0)",
    "G7_B_DequePopleft": "deque\n.popleft()",
    # G8
    "G8_A_ManualDict":  "Manual\nDict",
    "G8_B_DefaultDict": "default\ndict",
    # G9
    "G9_A_IfElse":      "for +\nif-else",
    "G9_B_While":       "while\nloop",
    "G9_C_DoWhile":     "do-while\nsim",
    # IDLE
    "IDLE_Baseline":    "Idle\nBaseline",
}

GROUP_TITLES = {
    "G1": "G1 — Summation: Manual Loop vs Built-in vs NumPy",
    "G2": "G2 — Filtering: Manual Loop vs filter() vs List Comp",
    "G3": "G3 — Transformation: Manual Loop vs map() vs List Comp",
    "G4": "G4 — Recursion vs Iteration (Factorial)",
    "G5": "G5 — Membership Testing: List O(N) vs Set O(1)",
    "G6": "G6 — String Building: += vs join()",
    "G7": "G7 — Queue Operations: list.pop(0) vs deque.popleft()",
    "G8": "G8 — Conditional Lookup: Manual Dict vs defaultdict",
    "G9": "G9 — Loop Constructs: for/if-else vs while vs do-while",
    "IDLE": "IDLE Baseline Measurement",
}

# ------------------------------------------------------------------
# Load and average data
# ------------------------------------------------------------------
data = defaultdict(lambda: defaultdict(list))

with open(FILE) as f:
    for r in csv.DictReader(f):
        op    = r["Operation_ID"]
        co2   = float(r["CO2_Emissions_g"])
        group = op.split("_")[0]
        data[group][op].append(co2)

avg_data = {}
for group in data:
    avg_data[group] = {op: sum(v) / len(v) for op, v in data[group].items()}

# ------------------------------------------------------------------
# Plot each group
# ------------------------------------------------------------------
for group in sorted(avg_data):
    ops    = list(avg_data[group].keys())
    values = [avg_data[group][op] for op in ops]
    labels = [SHORT_LABELS.get(op, op) for op in ops]

    # Identify winner (lowest CO2)
    min_idx = values.index(min(values))
    colors  = ["#2e8b57" if i == min_idx else "#4a90d9" for i in range(len(ops))]

    fig, ax = plt.subplots(figsize=(max(6, len(ops) * 1.8), 5))
    bars = ax.bar(labels, values, color=colors, edgecolor="white", linewidth=0.8)

    # Value labels on top of bars
    for bar, val in zip(bars, values):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() * 1.01,
            f"{val:.4f}",
            ha="center", va="bottom", fontsize=8, color="#333333"
        )

    title = GROUP_TITLES.get(group, f"{group} Benchmark")
    ax.set_title(title, fontsize=11, fontweight="bold", pad=12)
    ax.set_ylabel("Average CO₂ (g) per benchmark window", fontsize=9)
    ax.set_xlabel("Implementation", fontsize=9)
    ax.tick_params(axis="x", labelsize=9)
    ax.tick_params(axis="y", labelsize=8)

    # Legend
    from matplotlib.patches import Patch
    legend_elements = [
        Patch(facecolor="#2e8b57", label="Most efficient (lowest CO₂)"),
        Patch(facecolor="#4a90d9", label="Other variants"),
    ]
    ax.legend(handles=legend_elements, fontsize=8, loc="upper right")

    # India grid note
    ax.text(
        0.01, 0.97,
        "India grid intensity: 708 gCO₂eq/kWh (offline CodeCarbon)",
        transform=ax.transAxes, fontsize=7, color="#888888",
        va="top"
    )

    plt.tight_layout()
    plt.savefig(f"{group}_benchmark.png", dpi=120, bbox_inches="tight")
    plt.close()
    print(f"Saved {group}_benchmark.png")

print("\nAll plots saved.")
