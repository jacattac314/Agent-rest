"""
Inject minimal Google-style docstrings into Python source files.
Detects actual indentation from the def/class line and inserts at +4 spaces.
"""
import ast, re, sys
from pathlib import Path

SKIP_DIRS = {".git","__pycache__","node_modules",".venv","venv",
             "dist","build",".next","alembic",".github"}

def _leading(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]

def _snake_words(name): return re.sub(r'_+',' ',name).strip()
def _camel_words(name): return re.sub(r'(?<=[a-z])(?=[A-Z])',' ',name)

def _mod_doc(path: Path) -> str:
    stem = path.stem
    if stem == "__init__":
        return f'"""Package init for {path.parent.name}."""\n'
    return f'"""{_snake_words(stem).capitalize()} module."""\n'

def _class_doc(name: str, indent: str) -> str:
    return f'{indent}    """{_camel_words(name)}."""\n'

def _func_doc(name: str, node, indent: str) -> str:
    inner = indent + "    "
    words = _snake_words(name).capitalize()
    args = [a.arg for a in node.args.args if a.arg not in ("self","cls")]
    if not args:
        return f'{inner}"""{words}."""\n'
    return (f'{inner}"""{words}.\n\n{inner}Args:\n'
            + "".join(f'{inner}    {a}: Description.\n' for a in args)
            + f'{inner}"""\n')

def patch(path: Path) -> bool:
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return False

    lines = src.splitlines(keepends=True)

    # Collect insertions as (line_index_to_insert_after, text)
    inserts: list[tuple[int, str]] = []  # line_index (0-based), text

    has_mod = (tree.body and isinstance(tree.body[0], ast.Expr)
               and isinstance(tree.body[0].value, ast.Constant))
    if not has_mod and tree.body:
        inserts.append((0, _mod_doc(path)))  # 0 = insert before line 0

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        has_doc = (node.body and isinstance(node.body[0], ast.Expr)
                   and isinstance(node.body[0].value, ast.Constant))
        if has_doc:
            continue

        # Find the last line of the signature (handle multi-line defs)
        # The body starts at node.body[0].lineno
        body_start = node.body[0].lineno - 1  # 0-based index of first body line
        def_line = node.lineno - 1            # 0-based index of def/class line
        indent = _leading(lines[def_line])

        if isinstance(node, ast.ClassDef):
            doc = _class_doc(node.name, indent)
        else:
            doc = _func_doc(node.name, node, indent)

        # Insert the docstring as the first thing in the body
        inserts.append((body_start, doc))

    if not inserts:
        return False

    # Separate module-level (line_idx==0 but actually prepend) from others
    mod_inserts = [(idx, txt) for idx, txt in inserts if idx == 0 and not txt.startswith('    ')]
    node_inserts = sorted(
        [(idx, txt) for idx, txt in inserts if not (idx == 0 and not txt.startswith('    '))],
        key=lambda x: x[0], reverse=True
    )

    for idx, txt in node_inserts:
        lines.insert(idx, txt)

    if mod_inserts:
        lines.insert(0, mod_inserts[0][1])

    path.write_text("".join(lines), encoding="utf-8")
    return True


def run(repo_root: str):
    root = Path(repo_root)
    changed = []
    for fp in sorted(root.rglob("*.py")):
        if any(part in SKIP_DIRS for part in fp.parts):
            continue
        if patch(fp):
            changed.append(str(fp.relative_to(root)))
    return changed

if __name__ == "__main__":
    for repo in sys.argv[1:]:
        changed = run(repo)
        print(f"\n{repo}: {len(changed)} files patched")
        for f in changed:
            print(f"  + {f}")
