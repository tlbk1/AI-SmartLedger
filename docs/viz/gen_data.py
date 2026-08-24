"""gen_data.py — 从项目源码 AST 生成代码图谱数据(graph_data.js)

节点:Module(文件)/ Class / Function(含类方法)
边:DEFINES(模块→定义)、DEFINES_METHOD(类→方法)、IMPORTS(模块→模块)、CALLS(函数→函数)
用法:.venv/Scripts/python.exe docs/viz/gen_data.py
"""

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # docs/viz/ → 项目根
PY_FILES = sorted(ROOT.glob("*.py")) + sorted((ROOT / "tests").glob("*.py"))

nodes = []   # {id, name, label, file}
edges = []   # (type, source, target)


def add_node(name: str, label: str, file: str) -> int:
    nid = len(nodes)
    nodes.append({"id": nid, "name": name, "label": label, "file": file})
    return nid


def rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


# ───────── Pass 1:模块节点、import 表、顶层定义 ─────────
mod_ids = {}    # stem -> Module 节点 id
mod_stem = {}   # Path -> stem
func_ids = {}   # (stem, qualname) -> id,qualname 为 "f" 或 "Class.m"
cls_ids = {}    # (stem, "Class") -> id
imports_of = {} # stem -> {alias: ("mod", 目标模块) | ("sym", 来源模块, 符号名)}

for p in PY_FILES:
    stem = p.parent.name if p.name == "__init__.py" else p.stem
    mod_stem[p] = stem
    mod_ids[stem] = add_node(stem, "Module", rel(p))

project_mods = set(mod_ids)

for p in PY_FILES:
    stem = mod_stem[p]
    fpath = rel(p)
    tree = ast.parse(p.read_text(encoding="utf-8"))
    imports_of[stem] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                base = a.name.split(".")[0]
                if base in project_mods:
                    imports_of[stem][a.asname or base] = ("mod", base)
        elif isinstance(node, ast.ImportFrom) and node.module:
            base = node.module.split(".")[-1]
            if base in project_mods:
                for a in node.names:
                    imports_of[stem][a.asname or a.name] = ("sym", base, a.name)

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fid = add_node(node.name, "Function", fpath)
            func_ids[(stem, node.name)] = fid
            edges.append(("DEFINES", mod_ids[stem], fid))
        elif isinstance(node, ast.ClassDef):
            cid = add_node(node.name, "Class", fpath)
            cls_ids[(stem, node.name)] = cid
            edges.append(("DEFINES", mod_ids[stem], cid))
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    fid = add_node(sub.name, "Function", fpath)
                    func_ids[(stem, node.name + "." + sub.name)] = fid
                    edges.append(("DEFINES_METHOD", cid, fid))

# IMPORTS 边(模块间)
for stem, imp in imports_of.items():
    for kind in imp.values():
        target = mod_ids.get(kind[1]) if kind[0] == "mod" else (
            func_ids.get((kind[1], kind[2])) or cls_ids.get((kind[1], kind[2])))
        if kind[0] == "mod" and target is not None:
            edges.append(("IMPORTS", mod_ids[stem], target))


# ───────── Pass 2:调用解析 ─────────
class Collector(ast.NodeVisitor):
    """遍历语法树,把 Call 归属到最近的函数/方法/模块,解析为项目内目标。"""

    def __init__(self, stem: str):
        self.stem = stem
        self.stack = [mod_ids[stem]]  # 归属节点栈,栈底是模块
        self.classes = []             # 当前类名栈

    @property
    def cur(self):
        return self.stack[-1]

    def visit_ClassDef(self, node):
        self.classes.append(node.name)
        self.stack.append(cls_ids[(self.stem, node.name)])
        self.generic_visit(node)
        self.stack.pop()
        self.classes.pop()

    def visit_FunctionDef(self, node):
        qual = node.name
        if self.classes:
            qual = self.classes[-1] + "." + node.name
        fid = func_ids.get((self.stem, qual))
        if fid is not None:
            self.stack.append(fid)
            self.generic_visit(node)
            self.stack.pop()
        else:
            self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node):
        target = self.resolve(node.func)
        if target is not None:
            edges.append(("CALLS", self.cur, target))
        self.generic_visit(node)

    def resolve(self, f):
        imp = imports_of[self.stem]
        if isinstance(f, ast.Name):
            n = f.id
            if n in imp:
                kind = imp[n]
                if kind[0] == "sym":
                    return func_ids.get((kind[1], kind[2])) or cls_ids.get((kind[1], kind[2]))
                return None
            if self.classes:
                q = self.classes[-1] + "." + n
                if (self.stem, q) in func_ids:
                    return func_ids[(self.stem, q)]
            if (self.stem, n) in func_ids:
                return func_ids[(self.stem, n)]
            return cls_ids.get((self.stem, n))
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
            base, attr = f.value.id, f.attr
            if base in imp and imp[base][0] == "mod":
                m = imp[base][1]
                return func_ids.get((m, attr)) or cls_ids.get((m, attr))
            if base in ("self", "cls") and self.classes:
                return func_ids.get((self.stem, self.classes[-1] + "." + attr))
        return None


for p in PY_FILES:
    Collector(mod_stem[p]).visit(ast.parse(p.read_text(encoding="utf-8")))

# ───────── 输出 ─────────
seen = set()
uniq = []
for t, s, d in edges:
    if s != d and (t, s, d) not in seen:
        seen.add((t, s, d))
        uniq.append({"source": s, "target": d, "type": t})

out = "window.GRAPH_DATA = " + json.dumps(
    {"nodes": nodes, "edges": uniq}, ensure_ascii=False, indent=1) + ";\n"
(Path(__file__).parent / "graph_data.js").write_text(out, encoding="utf-8")

by_label = {}
for n in nodes:
    by_label[n["label"]] = by_label.get(n["label"], 0) + 1
by_type = {}
for e in uniq:
    by_type[e["type"]] = by_type.get(e["type"], 0) + 1
print(f"nodes={len(nodes)} edges={len(uniq)}")
print("labels:", by_label)
print("edge types:", by_type)
