from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path


def test_bot_modules_have_no_circular_dependencies() -> None:
    bot_dir = Path("bot")
    modules = {
        f"bot.{path.stem}": path
        for path in bot_dir.glob("*.py")
    }
    edges: dict[str, set[str]] = defaultdict(set)

    for module_name, path in modules.items():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        collector = _ImportCollector()
        collector.visit(tree)
        edges[module_name].update(collector.imports)

    visiting: set[str] = set()
    visited: set[str] = set()

    def walk(module_name: str) -> None:
        if module_name in visited or module_name not in modules:
            return
        if module_name in visiting:
            raise AssertionError(f"Circular dependency detected at {module_name}")

        visiting.add(module_name)
        for dependency in edges[module_name]:
            walk(dependency)
        visiting.remove(module_name)
        visited.add(module_name)

    for module_name in modules:
        walk(module_name)


class _ImportCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.imports: set[str] = set()
        self._skip_depth = 0

    def visit_If(self, node: ast.If) -> None:
        if self._is_type_checking_guard(node.test):
            self._skip_depth += 1
            for child in node.body:
                self.visit(child)
            self._skip_depth -= 1
            for child in node.orelse:
                self.visit(child)
            return
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if self._skip_depth == 0 and node.module and node.module.startswith("bot."):
            self.imports.add(node.module)

    def visit_Import(self, node: ast.Import) -> None:
        if self._skip_depth == 0:
            for alias in node.names:
                if alias.name.startswith("bot."):
                    self.imports.add(alias.name)

    def _is_type_checking_guard(self, node: ast.expr) -> bool:
        return isinstance(node, ast.Name) and node.id == "TYPE_CHECKING"
