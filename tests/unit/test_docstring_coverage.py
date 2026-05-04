import ast
from pathlib import Path

DOCSTRING_ROOTS = (Path("pyrallel_consumer"), Path("benchmarks"))


def test_runtime_and_benchmark_functions_have_docstrings() -> None:
    """Guard navigability of production and benchmark helper code."""
    missing: list[str] = []

    for root in DOCSTRING_ROOTS:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(
                    node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    continue
                if node.name.startswith("__") and node.name.endswith("__"):
                    continue
                if ast.get_docstring(node) is None:
                    missing.append(f"{path}:{node.lineno}:{node.name}")

    assert missing == []
