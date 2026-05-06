import ast, csv, sys, json
from dataclasses import dataclass, field
from typing import Optional

DATASET = "emissions_dataset.csv"
DEFAULT_CALLS = 1000

# ---------- DATA ----------
@dataclass
class Entry:
    op: str
    n: int
    co2: float
    reps: int

    @property
    def per_op(self):
        return max(self.co2 / self.reps, 1e-9) if self.reps else 1e-9


def load():
    db = {}
    with open(DATASET) as f:
        for r in csv.DictReader(f):
            e = Entry(
                r["Operation_ID"],
                int(r["Input_Size_N"]),
                float(r["CO2_Emissions_g"]),
                int(r["Repeats_In_Window"])
            )
            db.setdefault(e.op, []).append(e)
    return db


# ---------- LOOKUP ----------
def lookup(db, op):
    if op not in db:
        return None
    valid = [e for e in db[op] if e.co2 > 0]
    if not valid:
        return None
    return max(valid, key=lambda x: x.n)


# ---------- CALL ESTIMATION ----------
def estimate(node):
    calls = 1
    p = node
    while hasattr(p, "parent"):
        p = p.parent
        if isinstance(p, ast.For):
            if isinstance(p.iter, ast.Call):
                if p.iter.args and isinstance(p.iter.args[-1], ast.Constant):
                    calls *= p.iter.args[-1].value
                else:
                    calls *= DEFAULT_CALLS
    return calls


# ---------- FINDING ----------
@dataclass
class Finding:
    line: int
    label: str
    dirty: str
    clean: str
    de: Optional[Entry]
    ce: Optional[Entry]
    total_dirty: float = 0.0
    total_clean: float = 0.0
    range: tuple = field(default_factory=lambda: (0, 0))
    conf: str = "MEDIUM"


# ---------- DETECTOR ----------
class Detector(ast.NodeVisitor):
    def __init__(self, db):
        self.db = db
        self.findings = []

    # -------- G1 + G3 --------
    def visit_For(self, node):
        for stmt in node.body:
            # G1: manual sum
            if isinstance(stmt, ast.AugAssign) and isinstance(stmt.op, ast.Add):
                self.emit(node, "G1", "G1_A_Manual", "G1_B_Sum")

            # G3: manual map
            if isinstance(stmt, ast.Assign):
                self.emit(node, "G3", "G3_A_Manual", "G3_C_ListComp")

        self.generic_visit(node)

    # -------- G2 --------
    def visit_If(self, node):
        parent = getattr(node, "parent", None)
        if isinstance(parent, ast.For):
            self.emit(node, "G2", "G2_A_Manual", "G2_C_ListComp")
        self.generic_visit(node)

    # -------- G5 --------
    def visit_Compare(self, node):
        if isinstance(node.ops[0], ast.In):
            self.emit(node, "G5", "G5_A_ListLookup", "G5_B_SetLookup")
        self.generic_visit(node)

    # -------- G6 --------
    def visit_AugAssign(self, node):
        if isinstance(node.op, ast.Add):
            self.emit(node, "G6", "G6_A_StringConcat", "G6_B_StringJoin")
        self.generic_visit(node)

    # -------- G7 --------
    def visit_Call(self, node):
        if isinstance(node.func, ast.Attribute):
            if node.func.attr == "pop" and node.args:
                if isinstance(node.args[0], ast.Constant) and node.args[0].value == 0:
                    self.emit(node, "G7", "G7_A_ListPop0", "G7_B_DequePopleft")
        self.generic_visit(node)

    # -------- G9 --------
    def visit_While(self, node):
        self.emit(node, "G9", "G9_B_While", "G9_A_IfElse")
        self.generic_visit(node)

    # ---------- EMIT ----------
    def emit(self, node, label, dirty, clean):
        de = lookup(self.db, dirty)
        ce = lookup(self.db, clean)

        f = Finding(node.lineno, label, dirty, clean, de, ce)

        calls = estimate(node)

        if de and ce:
            f.total_dirty = de.per_op * calls
            f.total_clean = ce.per_op * calls
            f.range = (f.total_dirty * 0.7, f.total_dirty * 1.3)

        f.conf = "HIGH" if calls > 1 else "MEDIUM"

        self.findings.append(f)


# ---------- MAIN ----------
def run(file, use_json=False):
    tree = ast.parse(open(file).read())

    for n in ast.walk(tree):
        for c in ast.iter_child_nodes(n):
            c.parent = n

    db = load()
    d = Detector(db)
    d.visit(tree)

    if use_json:
        output = []
        for f in d.findings:
            output.append({
                "line": f.line,
                "pattern": f.label,
                "dirty": f.dirty,
                "clean": f.clean,
                "confidence": f.conf
            })
        print(json.dumps(output, indent=2))
    else:
        for f in d.findings:
            print(f"\nLine {f.line} | {f.label}")
            print("Total:", round(f.total_dirty, 6), "->", round(f.total_clean, 6))
            print("Range:", round(f.range[0], 6), "to", round(f.range[1], 6))
            print("Confidence:", f.conf)


if __name__ == "__main__":
    file = sys.argv[1]
    use_json = len(sys.argv) > 2 and sys.argv[2] == "--json"
    run(file, use_json)