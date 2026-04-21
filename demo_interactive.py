"""
demo_interactive.py
-------------------
Live interactive demo for the Carbon-Aware Code Analyzer.
Type or paste Python code, press Enter on a blank line to analyze.
Or pick a pre-loaded scenario by number.

Run:
    python demo_interactive.py
"""

import ast
import os
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from carbon_analyzer import analyze, VarTypeVisitor, PatternDetector, load_dataset, DATASET_PATH

try:
    from rich.console import Console
    from rich.syntax import Syntax
    from rich.panel import Panel
    _console = Console(legacy_windows=False)
    _RICH = True
except ImportError:
    _RICH = False

# ---------------------------------------------------------------------------
# Pre-loaded scenarios
# Each entry: (title, description, dirty_code, clean_code)
# ---------------------------------------------------------------------------
SCENARIOS = [
    (
        "Permission Check  --  list vs set membership",
        "The classic O(N) vs O(1) membership lookup.\n"
        "Your panel example: checking if a user_id is in allowed_members.",
        """\
ALLOWED_USERS = [1001, 1002, 1003, 1004, 1005,
                 1006, 1007, 1008, 1009, 1010]

def check_permission(user_id):
    if user_id in ALLOWED_USERS:
        return True
    return False
""",
        """\
# Fixed: convert the list to a set once at module level
ALLOWED_USERS = {1001, 1002, 1003, 1004, 1005,
                 1006, 1007, 1008, 1009, 1010}

def check_permission(user_id):
    if user_id in ALLOWED_USERS:   # now O(1)
        return True
    return False
"""
    ),

    (
        "Job Queue  --  list.pop(0) vs deque  (96% reduction)",
        "The most dramatic finding in the dataset.\n"
        "list.pop(0) shifts every element left: O(N) per call, O(N^2) to drain.",
        """\
def process_jobs(pending):
    queue = list(pending)
    results = []
    while queue:
        job = queue.pop(0)
        results.append(job * 2)
    return results
""",
        """\
from collections import deque

def process_jobs(pending):
    queue = deque(pending)     # O(1) front-removal
    results = []
    while queue:
        job = queue.popleft()
        results.append(job * 2)
    return results
"""
    ),

    (
        "Data Aggregation  --  manual loop vs sum()",
        "Every Python developer writes this loop at some point.\n"
        "The built-in sum() is implemented in C and consistently more efficient.",
        """\
def total_revenue(transactions):
    total = 0
    for amount in transactions:
        total += amount
    return total
""",
        """\
def total_revenue(transactions):
    return sum(transactions)   # C-level, no interpreter loop overhead
"""
    ),

    (
        "Filter Active Users  --  manual loop vs list comprehension",
        "Loop + if + append is the most common manual filter pattern.\n"
        "List comprehensions are faster because they avoid repeated .append() calls.",
        """\
def get_active_users(users):
    active = []
    for user in users:
        if user["active"]:
            active.append(user)
    return active
""",
        """\
def get_active_users(users):
    return [user for user in users if user["active"]]
"""
    ),

    (
        "Report Builder  --  string += vs join()",
        "String += in a loop forces a full string copy on each iteration.\n"
        "Collect parts in a list, then join once at the end.",
        """\
def build_report(entries):
    report = ""
    for entry in entries:
        report += f"[{entry['id']}] {entry['name']}: {entry['score']}\\n"
    return report
""",
        """\
def build_report(entries):
    lines = [f"[{e['id']}] {e['name']}: {e['score']}" for e in entries]
    return "\\n".join(lines)
"""
    ),

    (
        "Fibonacci  --  recursive vs iterative",
        "Naive recursion calls itself twice per step: O(2^N) time.\n"
        "An iterative version avoids call-stack overhead entirely.",
        """\
def fibonacci(n):
    if n <= 1:
        return n
    return fibonacci(n - 1) + fibonacci(n - 2)
""",
        """\
def fibonacci(n):
    if n <= 1:
        return n
    a, b = 0, 1
    for _ in range(2, n + 1):
        a, b = b, a + b
    return b
"""
    ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
SEP = "-" * 64


def _print(text="", **kw):
    if _RICH:
        _console.print(text, **kw)
    else:
        print(text)


def _show_code(code: str, title: str = ""):
    if _RICH:
        syntax = Syntax(code.strip(), "python", theme="monokai",
                        line_numbers=True, padding=(0, 1))
        _console.print(Panel(syntax, title=title, border_style="dim"))
    else:
        print(f"  {title}")
        for i, line in enumerate(code.strip().splitlines(), 1):
            print(f"  {i:3}  {line}")
        print()


def _analyze_code(code: str):
    """Parse, analyze, and print findings for an in-memory code string."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        _print(f"[red]  Syntax error in your code: {e}[/red]" if _RICH
               else f"  Syntax error: {e}")
        return

    db = load_dataset(DATASET_PATH)
    vt = VarTypeVisitor()
    vt.visit(tree)
    det = PatternDetector(vt, db)
    det.visit(tree)
    findings = sorted(det.findings, key=lambda f: f.line)

    if not findings:
        _print("\n[bold green]  No issues detected. This code looks clean![/bold green]"
               if _RICH else "\n  No issues detected. This code looks clean!")
        return

    _print(f"\n[bold]  {len(findings)} issue(s) found[/bold]" if _RICH
           else f"\n  {len(findings)} issue(s) found")

    for i, f in enumerate(findings, 1):
        conf_colour = {"CONFIRMED": "bold green",
                       "LIKELY": "yellow",
                       "POSSIBLE": "dim yellow"}.get(f.confidence, "white")
        conf_sym = {"CONFIRMED": "*", "LIKELY": "o", "POSSIBLE": "o"}.get(f.confidence, "?")

        _print(f"\n  [bold]#{i}[/bold]  "
               f"Line [cyan]{f.line}[/cyan]  |  "
               f"[bold]{f.group_label}[/bold]  |  "
               f"[{conf_colour}]{conf_sym} {f.confidence}[/{conf_colour}]"
               if _RICH else
               f"\n  #{i}  Line {f.line}  |  {f.group_label}  |  {conf_sym} {f.confidence}")

        _print(f"  [red]Issue:[/red] {f.description}" if _RICH
               else f"  Issue: {f.description}")

        if f.dirty_co2 and f.clean_co2:
            def fmt(g):
                if g >= 1e-3: return f"{g*1e3:.2f} mg"
                if g >= 1e-6: return f"{g*1e6:.2f} ug"
                return f"{g*1e9:.2f} ng"

            _print(f"  [bold]CO2 per call:[/bold]  "
                   f"[red]Current {fmt(f.dirty_co2)}[/red]  ->  "
                   f"[green]Optimized {fmt(f.clean_co2)}[/green]"
                   if _RICH else
                   f"  CO2/call:  Current {fmt(f.dirty_co2)}  ->  Optimized {fmt(f.clean_co2)}")

            if f.reduction_pct and f.reduction_pct > 0:
                bar = "#" * int(f.reduction_pct / 5)
                _print(f"  [bold green]  v {f.reduction_pct:.1f}% CO2 reduction  {bar}[/bold green]"
                       if _RICH else
                       f"    v {f.reduction_pct:.1f}% reduction  {bar}")

        _print("  [bold yellow]Quick Fix:[/bold yellow]" if _RICH
               else "  Quick Fix:")
        for line in f.suggestion.splitlines():
            _print(f"    {line}")

        if f.confidence == "POSSIBLE":
            _print("  [dim yellow]  Note: type could not be confirmed "
                   "statically -- applies only if the collection is a list.[/dim yellow]"
                   if _RICH else
                   "  Note: type not confirmed -- applies if collection is a list.")


# ---------------------------------------------------------------------------
# Main interactive loop
# ---------------------------------------------------------------------------
def main():
    os.system("cls" if os.name == "nt" else "clear")
    _print()
    _print("[bold blue]" + "=" * 64 + "[/bold blue]" if _RICH else "=" * 64)
    _print("[bold]  Carbon-Aware Code Analyzer[/bold]  |  "
           "[dim]India Grid (IND)  ~708 gCO2eq / kWh[/dim]"
           if _RICH else "  Carbon-Aware Code Analyzer  |  India Grid (IND)")
    _print("[bold blue]" + "=" * 64 + "[/bold blue]" if _RICH else "=" * 64)
    _print()

    while True:
        _print("[bold]Mode:[/bold]" if _RICH else "Mode:")
        _print("  [1]  Pre-loaded demo scenarios" if _RICH
               else "  [1]  Pre-loaded demo scenarios")
        _print("  [2]  Type / paste your own code")
        _print("  [q]  Quit")
        _print()

        choice = input("  > ").strip().lower()

        if choice == "q":
            _print("\n  Goodbye.\n")
            break

        elif choice == "1":
            _scenario_menu()

        elif choice == "2":
            _custom_code_mode()

        else:
            _print("[red]  Invalid choice. Enter 1, 2, or q.[/red]"
                   if _RICH else "  Invalid choice.")


def _scenario_menu():
    while True:
        _print()
        _print("[bold]Pre-loaded scenarios:[/bold]" if _RICH
               else "Pre-loaded scenarios:")
        for i, (title, *_) in enumerate(SCENARIOS, 1):
            _print(f"  [{i}]  {title}")
        _print("  [b]  Back")
        _print()

        choice = input("  > ").strip().lower()
        if choice == "b":
            return

        try:
            idx = int(choice) - 1
            if not 0 <= idx < len(SCENARIOS):
                raise ValueError
        except ValueError:
            _print("[red]  Enter a number between 1 and "
                   f"{len(SCENARIOS)}, or b.[/red]" if _RICH
                   else f"  Enter 1-{len(SCENARIOS)} or b.")
            continue

        title, desc, dirty, clean = SCENARIOS[idx]

        _print()
        _print("[bold blue]" + SEP + "[/bold blue]" if _RICH else SEP)
        _print(f"[bold]  {title}[/bold]" if _RICH else f"  {title}")
        _print("[bold blue]" + SEP + "[/bold blue]" if _RICH else SEP)
        _print(f"\n  {desc}\n")

        _print("[bold]--- Code under analysis ---[/bold]" if _RICH
               else "--- Code under analysis ---")
        _show_code(dirty, "Dirty Code")

        input("  Press Enter to run analysis...")

        _print()
        _print("[bold]--- Analysis Results ---[/bold]" if _RICH
               else "--- Analysis Results ---")
        _analyze_code(dirty)

        _print()
        input("  Press Enter to see the optimized version...")
        _print()
        _print("[bold]--- Optimized Code ---[/bold]" if _RICH
               else "--- Optimized Code ---")
        _show_code(clean, "Clean Code")

        _print()
        input("  Press Enter to return to scenario list...")


def _custom_code_mode():
    _print()
    _print("[bold]Paste or type Python code below.[/bold]" if _RICH
           else "Paste or type Python code below.")
    _print("  Press [bold]Enter on a blank line[/bold] to analyze."
           if _RICH else "  Press Enter on a blank line to analyze.")
    _print("  Type [bold]back[/bold] on any line to return to menu.")
    _print()

    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.lower() == "back":
            return
        if line == "" and lines:
            break
        lines.append(line)

    if not lines:
        return

    code = "\n".join(lines)
    _print()
    _print("[bold]--- Analysis Results ---[/bold]" if _RICH
           else "--- Analysis Results ---")
    _analyze_code(code)
    _print()
    input("  Press Enter to continue...")


if __name__ == "__main__":
    main()
