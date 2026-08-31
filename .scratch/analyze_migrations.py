import ast, pathlib, collections
root = pathlib.Path("migrations/versions")
revs = {}
for p in sorted(root.glob("*.py")):
    try:
        tree = ast.parse(p.read_text(encoding="utf-8"))
    except Exception as e:
        print("PARSE FAIL", p, e); continue
    rev = down = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    if t.id == "revision" and isinstance(node.value, ast.Constant):
                        rev = str(node.value.value)
                    if t.id == "down_revision":
                        if isinstance(node.value, ast.Constant):
                            down = str(node.value.value)
                        elif isinstance(node.value, ast.Tuple):
                            down = tuple(str(e.value) for e in node.value.elts)
    revs[rev] = (p.name, down)

byrev = collections.defaultdict(list)
for rev, (name, down) in revs.items():
    byrev[rev].append(name)
print("DUPLICATE REVISIONS:")
for rev, names in byrev.items():
    if len(names) > 1:
        print(" ", rev, names)

referenced = set()
for rev, (name, down) in revs.items():
    if isinstance(down, tuple):
        referenced.update(down)
    elif down:
        referenced.add(down)
print("HEADS (not referenced):")
for rev, (name, down) in revs.items():
    if rev not in referenced:
        print(" ", rev, "->", name)

print("MISSING down_revision refs:")
for rev, (name, down) in revs.items():
    ds = [down] if isinstance(down, str) else (down or [])
    for d in ds:
        if d and d not in revs:
            print(" ", name, "down", d, "MISSING")
