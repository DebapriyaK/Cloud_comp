"""
carbon_analyzer.py
-------------------
Static carbon-impact analyzer for Python source files.

Pipeline (mirrors S3 of the methodology document):
  1. Parse source into an AST.
  2. Walk the AST to build a variable-type map (list / set / str / unknown).
  3. Walk the AST again to detect patterns matching the benchmark taxonomy.
  4. For each pattern, query emissions_dataset.csv with the closest N,
     compute normalized CO2 per operation call, and calculate delta.
  5. Print a formatted report with quick-fix suggestions.

Usage:
    python carbon_analyzer.py <source_file.py>

Two methodological concerns addressed here (per the methodology discussion):
  A) Type-inference uncertainty: When a variable's type cannot be confirmed
     statically (e.g. it is a function parameter), the finding is flagged
     as "POSSIBLE" rather than "CONFIRMED" so the developer is not misled.
  B) Normalization: Raw CodeCarbon values are divided by Repeats_In_Window
     to produce a per-operation-call energy figure. This makes G5_A and
     G5_B comparable even though they had different measurement durations.
"""

import ast
import csv
import os
import sys
from dataclasses import dataclass, field
from typing import Optional

# -- Optional rich output; graceful fallback to plain text -----------------
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text
    _RICH = True
    # legacy_windows=False: use ANSI output, not the Win32 API which is
    # limited to the system codepage (cp1252 on many Windows installs).
    _console = Console(legacy_windows=False)
except ImportError:
    _RICH = False

DATASET_PATH       = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "emissions_dataset.csv")
DEFAULT_N          = 100_000  # used when size cannot be inferred statically
REDUCTION_THRESH   = 0.05     # suppress suggestions whose savings < 5 %


# ===========================================================================
# DATASET LAYER
# ===========================================================================

@dataclass
class ProfileEntry:
    operation_id: str
    group:        str
    n:            int
    energy_kwh:   float
    co2_g:        float
    repeats:      int

    @property
    def co2_per_op(self) -> float:
        """
        Normalised CO2 per single workload call (Sconcern B).
        Divides the raw window total by the number of repeats so that
        comparisons between variants with different loop counts are fair.
        """
        return self.co2_g / self.repeats if self.repeats else self.co2_g


def load_dataset(path: str) -> dict:
    """Return {operation_id: {N: ProfileEntry}} from the benchmark CSV."""
    db: dict = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            e = ProfileEntry(
                operation_id=row["Operation_ID"],
                group=row["Equivalence_Group"],
                n=int(row["Input_Size_N"]),
                energy_kwh=float(row["Energy_Consumed_kWh"]),
                co2_g=float(row["CO2_Emissions_g"]),
                repeats=int(row["Repeats_In_Window"]),
            )
            db.setdefault(e.operation_id, {})[e.n] = e
    return db


def lookup(db: dict, operation_id: str, n: int) -> Optional[ProfileEntry]:
    """Return the ProfileEntry for the N value closest to the requested n."""
    entries = db.get(operation_id)
    if not entries:
        return None
    return min(entries.values(), key=lambda e: abs(e.n - n))


# ===========================================================================
# VARIABLE TYPE TRACKER  (single-scope; sufficient for a demo)
# ===========================================================================

class VarTypeVisitor(ast.NodeVisitor):
    """
    Walks all Assign nodes in a module and records the inferred container
    type of each name: "list" | "set" | "str" | "dict" | "unknown".

    Limitation: this is flat (no per-function scope), so local variables
    that shadow outer names may be misclassified. For a full implementation
    a scope stack would be required.
    """

    def __init__(self):
        self.types: dict[str, str] = {}
        self.sizes: dict[str, int] = {}   # element count for list/set literals

    def visit_Assign(self, node: ast.Assign):
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name  = node.targets[0].id
            value = node.value

            if isinstance(value, ast.List):
                self.types[name] = "list"
                self.sizes[name] = len(value.elts)
            elif isinstance(value, ast.Set):
                self.types[name] = "set"
                self.sizes[name] = len(value.elts)
            elif isinstance(value, ast.Dict):
                self.types[name] = "dict"
            elif isinstance(value, ast.Constant) and isinstance(value.value, str):
                self.types[name] = "str"
                self.sizes[name] = len(value.value)
            elif isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
                cname = value.func.id
                if cname == "list":
                    self.types[name] = "list"
                elif cname == "set":
                    self.types[name] = "set"
                elif cname == "dict":
                    self.types[name] = "dict"
                elif cname == "str":
                    self.types[name] = "str"

        self.generic_visit(node)

    def type_of(self, name: str) -> str:
        return self.types.get(name, "unknown")

    def size_of(self, name: str) -> Optional[int]:
        return self.sizes.get(name)


# ===========================================================================
# FINDING  (one detected issue)
# ===========================================================================

@dataclass
class Finding:
    line:          int
    end_line:      int          # last line of the detected block
    group_label:   str
    dirty_op:      str
    clean_op:      str
    description:   str
    suggestion:    str
    estimated_n:   int
    confidence:    str          # "CONFIRMED" | "LIKELY" | "POSSIBLE"
    dirty_entry:   Optional[ProfileEntry] = field(default=None, repr=False)
    clean_entry:   Optional[ProfileEntry] = field(default=None, repr=False)

    @property
    def dirty_co2(self) -> Optional[float]:
        return self.dirty_entry.co2_per_op if self.dirty_entry else None

    @property
    def clean_co2(self) -> Optional[float]:
        return self.clean_entry.co2_per_op if self.clean_entry else None

    @property
    def reduction_pct(self) -> Optional[float]:
        if self.dirty_co2 and self.clean_co2 and self.dirty_co2 > 0:
            return (self.dirty_co2 - self.clean_co2) / self.dirty_co2 * 100
        return None

    @property
    def actual_n_used(self) -> Optional[int]:
        return self.dirty_entry.n if self.dirty_entry else None


# ===========================================================================
# PATTERN DETECTOR  (AST visitor)
# ===========================================================================

def _node_name(node) -> Optional[str]:
    if isinstance(node, ast.Name):      return node.id
    if isinstance(node, ast.Attribute): return node.attr
    return None


def _is_single_append(stmts: list) -> bool:
    """True when stmts is exactly [result.append(...)]."""
    if len(stmts) != 1:
        return False
    s = stmts[0]
    return (isinstance(s, ast.Expr) and
            isinstance(s.value, ast.Call) and
            isinstance(s.value.func, ast.Attribute) and
            s.value.func.attr == "append")


class PatternDetector(ast.NodeVisitor):
    """
    Walks the AST and emits Finding objects for each detected pattern.
    One finding per syntactic location -- duplicate lines are skipped.
    """

    def __init__(self, var_types: VarTypeVisitor, db: dict):
        self.var_types = var_types
        self.db        = db
        self.findings: list[Finding] = []
        self._seen_lines: set[int] = set()   # avoid double-reporting same line

    # ----------------------------------------------------------------------
    # For-loop patterns: G1, G2, G3, G6
    # ----------------------------------------------------------------------
    def visit_For(self, node: ast.For):
        if node.lineno in self._seen_lines:
            self.generic_visit(node)
            return

        n = self._iter_size(node.iter)
        body = node.body

        # -- G6: string += inside a loop  (check before G1 to avoid collision)
        for stmt in body:
            if isinstance(stmt, ast.AugAssign) and isinstance(stmt.op, ast.Add):
                target_name = _node_name(stmt.target)
                target_type = self.var_types.type_of(target_name or "")
                val_is_str  = (isinstance(stmt.value, ast.Constant) and
                               isinstance(stmt.value.value, str))
                if target_type == "str" or val_is_str:
                    self._emit(node.lineno, getattr(node, 'end_lineno', node.lineno), "G6 . String Building",
                               "G6_A_StringConcat", "G6_B_StringJoin",
                               'String concatenation with += inside a loop. '
                               'Each += may copy the entire accumulated string -> O(N2).',
                               'Collect parts in a list then join once:\n'
                               '    parts = []\n'
                               '    for ...: parts.append(chunk)\n'
                               '    result = "".join(parts)',
                               n, "CONFIRMED")
                    self._seen_lines.add(node.lineno)
                    self.generic_visit(node)
                    return

        # -- G1: manual accumulation  (for x in it: total += x)
        if len(body) == 1 and isinstance(body[0], ast.AugAssign):
            stmt = body[0]
            if (isinstance(stmt.op, ast.Add) and
                    isinstance(stmt.value, (ast.Name, ast.Attribute, ast.Subscript))):
                target_name = _node_name(stmt.target)
                if self.var_types.type_of(target_name or "") != "str":
                    self._emit(node.lineno, getattr(node, 'end_lineno', node.lineno), "G1 . Summation",
                               "G1_A_ManualLoop", "G1_B_BuiltinSum",
                               "Manual accumulation loop detected (total += item).",
                               "Replace with Python's built-in:\n"
                               "    total = sum(iterable)",
                               n, "LIKELY")
                    self._seen_lines.add(node.lineno)
                    self.generic_visit(node)
                    return

        # -- G2: manual filter  (if cond: result.append(x))
        if (len(body) == 1 and
                isinstance(body[0], ast.If) and
                _is_single_append(body[0].body) and
                not body[0].orelse):
            self._emit(node.lineno, getattr(node, 'end_lineno', node.lineno), "G2 . Filtering",
                       "G2_A_ManualLoop", "G2_C_ListComp",
                       "Manual filter loop: loop + if + append.",
                       "Replace with a list comprehension:\n"
                       "    result = [x for x in iterable if condition]",
                       n, "CONFIRMED")
            self._seen_lines.add(node.lineno)
            self.generic_visit(node)
            return

        # -- G3: manual transform  (result.append(expr))  -- no branch in body
        if (_is_single_append(body) and
                not any(isinstance(s, ast.If) for s in body)):
            self._emit(node.lineno, getattr(node, 'end_lineno', node.lineno), "G3 . Transformation",
                       "G3_A_ManualLoop", "G3_C_ListComp",
                       "Manual transformation loop: loop + append (no condition).",
                       "Replace with a list comprehension:\n"
                       "    result = [transform(x) for x in iterable]",
                       n, "CONFIRMED")
            self._seen_lines.add(node.lineno)

        self.generic_visit(node)

    # ----------------------------------------------------------------------
    # G4: Recursive function definition
    # ----------------------------------------------------------------------
    def visit_FunctionDef(self, node: ast.FunctionDef):
        fname = node.name
        for child in ast.walk(node):
            if isinstance(child, ast.Call) and _node_name(child.func) == fname:
                if node.lineno not in self._seen_lines:
                    self._emit(node.lineno, getattr(node, 'end_lineno', node.lineno), "G4 . Recursion",
                               "G4_A_Recursive", "G4_B_Iterative",
                               f"Recursive function '{fname}' detected. "
                               "Each call adds a frame to the call stack.",
                               f"Consider rewriting '{fname}' iteratively "
                               "to eliminate call-stack overhead:\n"
                               "    Use an explicit loop + local variable instead.",
                               100,   # use N=100 profile from G4 dataset
                               "CONFIRMED")
                    self._seen_lines.add(node.lineno)
                break
        self.generic_visit(node)

    # ----------------------------------------------------------------------
    # G5: Membership test on a list  (x in some_list)
    # Handles two sub-cases:
    #   CONFIRMED -- variable was initialized as a list literal in this file
    #   POSSIBLE  -- variable is a function parameter or has unknown type
    #               (Sconcern A: we do NOT assert it is a list)
    # ----------------------------------------------------------------------
    def visit_Compare(self, node: ast.Compare):
        for op, right in zip(node.ops, node.comparators):
            if not isinstance(op, ast.In):
                continue
            cname = _node_name(right)
            if not cname:
                continue

            ctype = self.var_types.type_of(cname)
            size  = self.var_types.size_of(cname)
            n     = size if (size and size > 10) else DEFAULT_N

            if ctype == "list":
                confidence  = "CONFIRMED"
                description = (f"Membership test 'x in {cname}' where '{cname}' "
                               f"is a list (confirmed). Linear O(N) scan.")
                suggestion  = (f"Convert '{cname}' to a set for O(1) lookups:\n"
                               f"    {cname} = set({cname})")

            elif ctype == "unknown":
                # Sconcern A: cannot confirm type -- flag as POSSIBLE, not CONFIRMED
                confidence  = "POSSIBLE"
                description = (f"Membership test 'x in {cname}' -- type of "
                               f"'{cname}' cannot be confirmed statically "
                               f"(likely a function parameter). "
                               f"If it is a list, this is an O(N) scan.")
                suggestion  = (f"If '{cname}' does not require ordering or duplicates,\n"
                               f"pass it as a set at the call site:\n"
                               f"    {cname} = set({cname})")
            else:
                continue    # already a set/dict -- no issue

            if node.lineno not in self._seen_lines:
                self._emit(node.lineno, getattr(node, 'end_lineno', node.lineno), "G5 . Membership",
                           "G5_A_ListLookup", "G5_B_SetLookup",
                           description, suggestion, n, confidence)
                self._seen_lines.add(node.lineno)

        self.generic_visit(node)

    # ----------------------------------------------------------------------
    # G7: list.pop(0)
    # ----------------------------------------------------------------------
    def visit_Call(self, node: ast.Call):
        if (isinstance(node.func, ast.Attribute) and
                node.func.attr == "pop" and
                len(node.args) == 1 and
                isinstance(node.args[0], ast.Constant) and
                node.args[0].value == 0):

            obj_name = _node_name(node.func.value)
            obj_type = self.var_types.type_of(obj_name or "")
            size     = self.var_types.size_of(obj_name or "") if obj_name else None
            n        = size if (size and size > 10) else DEFAULT_N
            conf     = "CONFIRMED" if obj_type == "list" else "LIKELY"

            if node.lineno not in self._seen_lines:
                self._emit(node.lineno, getattr(node, 'end_lineno', node.lineno), "G7 . Queue Operations",
                           "G7_A_ListPop0", "G7_B_DequePopleft",
                           f".pop(0) on '{obj_name or 'list'}' shifts every remaining "
                           f"element left -- O(N) per call, O(N2) to drain N elements.",
                           "Use collections.deque for O(1) front-removal:\n"
                           "    from collections import deque\n"
                           f"    {obj_name or 'q'} = deque({obj_name or 'q'})\n"
                           f"    {obj_name or 'q'}.popleft()",
                           n, conf)
                self._seen_lines.add(node.lineno)

        self.generic_visit(node)

    # ----------------------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------------------
    def _iter_size(self, iter_node) -> int:
        """Heuristic: estimate N from a for-loop's iterable."""
        # range(literal_N)  or  range(start, literal_N)
        if (isinstance(iter_node, ast.Call) and
                isinstance(iter_node.func, ast.Name) and
                iter_node.func.id == "range" and
                iter_node.args):
            arg = iter_node.args[-1]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, int):
                return arg.value

        # Named variable -- use its recorded literal size if available
        if isinstance(iter_node, ast.Name):
            s = self.var_types.size_of(iter_node.id)
            if s and s > 0:
                return s

        return DEFAULT_N

    def _emit(self, line, end_line, group_label, dirty_op, clean_op,
              description, suggestion, n, confidence):
        dirty_e = lookup(self.db, dirty_op, n)
        clean_e = lookup(self.db, clean_op, n)

        # concern B: normalize both entries to per-op cost before comparing
        if dirty_e and clean_e:
            reduction = (dirty_e.co2_per_op - clean_e.co2_per_op) / dirty_e.co2_per_op
            if reduction < REDUCTION_THRESH:
                if confidence in ("POSSIBLE", "LIKELY"):
                    return

        self.findings.append(Finding(
            line=line,
            end_line=end_line,
            group_label=group_label,
            dirty_op=dirty_op,
            clean_op=clean_op,
            description=description,
            suggestion=suggestion,
            estimated_n=n,
            confidence=confidence,
            dirty_entry=dirty_e,
            clean_entry=clean_e,
        ))


# ===========================================================================
# REPORTER
# ===========================================================================

_CONF_COLOUR = {
    "CONFIRMED": "bold green",
    "LIKELY":    "yellow",
    "POSSIBLE":  "dim yellow",
}

_CONF_SYMBOL = {
    "CONFIRMED": "*",
    "LIKELY":    "o",
    "POSSIBLE":  "o",
}

def _fmt_co2(g: float) -> str:
    if g >= 1:      return f"{g:.4f} g"
    if g >= 1e-3:   return f"{g*1e3:.4f} mg"
    if g >= 1e-6:   return f"{g*1e6:.4f} ug"
    return f"{g*1e9:.4f} ng"


def report_rich(source_path: str, findings: list[Finding]) -> None:
    c = _console

    c.print()
    c.print("[dim]" + "-" * 68 + "[/dim]")
    c.print(f"[bold blue]Carbon Analysis  .  [cyan]{os.path.basename(source_path)}[/cyan][/bold blue]")
    c.print("[dim]" + "-" * 68 + "[/dim]")
    c.print(f"  Dataset: [dim]{DATASET_PATH}[/dim]")
    c.print(f"  Grid   : India (IND)  ~708 gCO2eq / kWh")
    c.print(f"  Found  : [bold]{len(findings)}[/bold] pattern(s)")
    c.print()

    if not findings:
        c.print("[bold green]  No issues found.[/bold green]")
        return

    for i, f in enumerate(findings, 1):
        conf_style  = _CONF_COLOUR.get(f.confidence, "white")
        conf_symbol = _CONF_SYMBOL.get(f.confidence, "?")

        # -- Header --------------------------------------------------------
        c.print("[dim]" + "-" * 68 + "[/dim]")
        c.print(
            f"  [bold]#{i}[/bold]  "
            f"Line [bold cyan]{f.line}[/bold cyan]  |  "
            f"[bold]{f.group_label}[/bold]  |  "
            f"[{conf_style}]{conf_symbol} {f.confidence}[/{conf_style}]  |  "
            f"est. N = [italic]{f.estimated_n:,}[/italic]"
        )

        # -- Description ---------------------------------------------------
        c.print(f"\n  [bold red]Issue[/bold red]")
        c.print(f"    {f.description}")

        # -- Energy/CO2 comparison -----------------------------------------
        c.print(f"\n  [bold]Carbon Impact[/bold]  "
                f"[dim](per single operation call, normalised)[/dim]")

        if f.dirty_co2 is not None and f.clean_co2 is not None:
            actual_n = f"{f.actual_n_used:,}" if f.actual_n_used else "?"
            c.print(f"    [red]x Current  ({f.dirty_op})[/red]  "
                    f"CO2 ~ [bold]{_fmt_co2(f.dirty_co2)}[/bold]  [dim]@ N={actual_n}[/dim]")
            c.print(f"    [green]+ Optimal  ({f.clean_op})[/green]  "
                    f"CO2 ~ [bold]{_fmt_co2(f.clean_co2)}[/bold]  [dim]@ N={actual_n}[/dim]")

            if f.reduction_pct is not None:
                r = f.reduction_pct
                if r > 0:
                    bar = "#" * int(r / 5)
                    c.print(f"\n    [bold green]  v {r:.1f}% lower CO2  {bar}[/bold green]")
                    savings = f.dirty_co2 - f.clean_co2
                    c.print(f"    Saves ~ {_fmt_co2(savings)} per call")
                else:
                    c.print(f"    [dim]No measurable advantage at N={actual_n} "
                            f"(signal within noise floor -- savings appear at larger N)[/dim]")
        else:
            c.print("    [dim]No benchmark data available for this N -- "
                    "run the full benchmark suite to populate the dataset.[/dim]")

        # -- Suggestion ----------------------------------------------------
        c.print(f"\n  [bold yellow]Quick Fix[/bold yellow]")
        for line in f.suggestion.splitlines():
            c.print(f"    {line}")

        # -- Confidence note -----------------------------------------------
        if f.confidence == "POSSIBLE":
            c.print(
                f"\n  [dim yellow]  Note: Type of the collection could not be confirmed "
                f"statically (e.g. it is a function parameter). This suggestion "
                f"applies only if the argument is actually a list.[/dim yellow]"
            )
        c.print()

    c.print("[dim]" + "-" * 68 + "[/dim]")
    # -- Summary -----------------------------------------------------------
    confirmed = sum(1 for f in findings if f.confidence == "CONFIRMED")
    possible  = len(findings) - confirmed
    c.print(f"\n  [bold]Summary[/bold]")
    c.print(f"    {confirmed} confirmed  .  {possible} possible/likely")
    c.print()


def report_plain(source_path: str, findings: list[Finding]) -> None:
    sep = "-" * 68
    print(f"\n{'='*68}")
    print(f"  Carbon Analysis  .  {os.path.basename(source_path)}")
    print(f"  Grid: India (IND)  ~708 gCO2eq/kWh  |  {len(findings)} finding(s)")
    print(f"{'='*68}\n")

    for i, f in enumerate(findings, 1):
        sym = _CONF_SYMBOL.get(f.confidence, "?")
        print(sep)
        print(f"  #{i}  Line {f.line}  |  {f.group_label}  |  {sym} {f.confidence}"
              f"  |  est. N={f.estimated_n:,}")
        print(f"\n  Issue: {f.description}")

        if f.dirty_co2 is not None and f.clean_co2 is not None:
            an = f"{f.actual_n_used:,}" if f.actual_n_used else "?"
            print(f"\n  Carbon (per call @ N={an}):")
            print(f"    Current  ({f.dirty_op}): {_fmt_co2(f.dirty_co2)}")
            print(f"    Optimal  ({f.clean_op}): {_fmt_co2(f.clean_co2)}")
            if f.reduction_pct is not None and f.reduction_pct > 0:
                print(f"    Savings : {f.reduction_pct:.1f}% reduction")

        print(f"\n  Quick Fix:")
        for line in f.suggestion.splitlines():
            print(f"    {line}")

        if f.confidence == "POSSIBLE":
            print("\n  [Note: type could not be confirmed statically]")
        print()

    print(sep)


# ===========================================================================
# MAIN
# ===========================================================================

def analyze(source_path: str) -> list[Finding]:
    with open(source_path, encoding="utf-8") as f:
        source = f.read()

    tree = ast.parse(source, filename=source_path)

    # Pass 1: collect variable types from the whole module
    var_types = VarTypeVisitor()
    var_types.visit(tree)

    # Pass 2: detect patterns (requires the type map from pass 1)
    db        = load_dataset(DATASET_PATH)
    detector  = PatternDetector(var_types, db)
    detector.visit(tree)

    # Sort by source line for a top-to-bottom reading experience
    return sorted(detector.findings, key=lambda f: f.line)


def main() -> None:
    args = sys.argv[1:]
    json_mode = "--json" in args
    args = [a for a in args if a != "--json"]

    if not args:
        print("Usage: python carbon_analyzer.py [--json] <source_file.py>")
        sys.exit(1)

    path = args[0]
    if not os.path.isfile(path):
        # In JSON mode always output valid JSON so the extension can parse it
        if json_mode:
            import json
            print(json.dumps({"findings": [], "error": f"file not found: {path}"}))
        else:
            print(f"Error: file not found -- {path}")
        sys.exit(1)

    findings = analyze(path)

    if json_mode:
        import json
        def fmt_co2(g):
            if g is None: return None
            if g >= 1e-3: return f"{g*1e3:.2f} mg CO2"
            if g >= 1e-6: return f"{g*1e6:.2f} ug CO2"
            return f"{g*1e9:.2f} ng CO2"

        output = {
            "findings": [
                {
                    "line":         f.line,
                    "end_line":     f.end_line,
                    "group":        f.group_label,
                    "confidence":   f.confidence,
                    "description":  f.description,
                    "suggestion":   f.suggestion,
                    "dirty_op":     f.dirty_op,
                    "clean_op":     f.clean_op,
                    "reduction_pct": round(f.reduction_pct, 1) if f.reduction_pct else None,
                    "dirty_co2_fmt": fmt_co2(f.dirty_co2),
                    "clean_co2_fmt": fmt_co2(f.clean_co2),
                    "estimated_n":  f.estimated_n,
                }
                for f in findings
            ]
        }
        print(json.dumps(output))
        return

    if _RICH:
        report_rich(path, findings)
    else:
        report_plain(path, findings)


if __name__ == "__main__":
    main()
