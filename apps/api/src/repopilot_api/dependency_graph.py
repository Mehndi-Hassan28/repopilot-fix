"""Advanced Code Intelligence — Static Dependency Graph & Impact Analysis Engine.

Performs AST-driven static dependency analysis across repository files:
- File-level and symbol-level imports.
- Function, class, and method definitions with line ranges.
- Resolvable function call and reference relationships.
- Cross-file symbol resolution (resolved vs inferred vs unresolved/external).
- Reverse dependency lookup (consumers / dependents).
- Circular dependency cycle detection.
- Bounded bug impact analysis and test recommendation.
"""

from __future__ import annotations

import ast
import os
import re
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from typing import Literal

RelationshipType = Literal["resolved", "inferred", "unresolved"]
EdgeKind = Literal["import", "call", "inheritance", "reference"]


@dataclass
class SymbolDef:
    name: str
    kind: str  # "function" | "class" | "method"
    file_path: str
    line: int
    end_line: int
    docstring: str | None = None
    parent_class: str | None = None
    parameters: list[str] = field(default_factory=list)


@dataclass
class ImportRef:
    source_file: str
    module: str
    imported_name: str
    alias: str | None
    line: int
    is_relative: bool
    level: int
    target_file: str | None = None
    relationship: RelationshipType = "unresolved"


@dataclass
class CallRef:
    source_file: str
    source_symbol: str | None
    called_name: str
    line: int
    target_file: str | None = None
    target_symbol: str | None = None
    relationship: RelationshipType = "unresolved"


@dataclass
class DependencyEdge:
    source: str
    target: str
    kind: EdgeKind
    relationship: RelationshipType
    line: int
    source_symbol: str | None = None
    target_symbol: str | None = None
    evidence: str = ""


@dataclass
class FileNode:
    path: str
    language: str
    is_test: bool
    symbols: list[dict] = field(default_factory=list)
    imports: list[dict] = field(default_factory=list)
    outgoing_files: list[str] = field(default_factory=list)
    incoming_files: list[str] = field(default_factory=list)
    has_circular_dependency: bool = False
    in_degree: int = 0
    out_degree: int = 0


@dataclass
class DependencyGraphResult:
    nodes: dict[str, dict]
    edges: list[dict]
    cycles: list[list[str]]
    symbol_index: dict[str, list[dict]]
    file_count: int
    edge_count: int
    cycle_count: int


@dataclass
class ImpactAnalysisResult:
    query: str
    root_cause_candidates: list[dict]
    affected_dependencies: list[dict]
    suggested_tests: list[dict]
    dependency_subgraph: dict
    disclaimer: str


class DependencyGraphBuilder:
    """Builds and analyzes a static dependency graph across repository files."""

    def __init__(self, files: dict[str, str]):
        self.files = files
        self.all_paths = set(files.keys())
        self.symbols_by_file: dict[str, list[SymbolDef]] = defaultdict(list)
        self.symbols_by_name: dict[str, list[SymbolDef]] = defaultdict(list)
        self.imports_by_file: dict[str, list[ImportRef]] = defaultdict(list)
        self.calls_by_file: dict[str, list[CallRef]] = defaultdict(list)
        self.edges: list[DependencyEdge] = []
        self._parsed = False

    def build(self) -> DependencyGraphResult:
        if not self._parsed:
            self._parse_all_files()
            self._resolve_imports()
            self._resolve_calls()
            self._build_edges()
            self._parsed = True

        cycles = self.detect_cycles()
        cycle_files = {f for cycle in cycles for f in cycle}

        nodes: dict[str, dict] = {}
        for path in sorted(self.all_paths):
            symbols = [asdict(s) for s in self.symbols_by_file.get(path, [])]
            imports = [asdict(i) for i in self.imports_by_file.get(path, [])]
            outgoing = sorted(
                {e.target for e in self.edges if e.source == path and e.target != path}
            )
            incoming = sorted(
                {e.source for e in self.edges if e.target == path and e.source != path}
            )
            lang = self._infer_language(path)
            is_test = self._is_test_file(path)

            node = FileNode(
                path=path,
                language=lang,
                is_test=is_test,
                symbols=symbols,
                imports=imports,
                outgoing_files=outgoing,
                incoming_files=incoming,
                has_circular_dependency=(path in cycle_files),
                in_degree=len(incoming),
                out_degree=len(outgoing),
            )
            nodes[path] = asdict(node)

        symbol_index: dict[str, list[dict]] = {}
        for name, syms in self.symbols_by_name.items():
            symbol_index[name] = [asdict(s) for s in syms]

        edge_dicts = [asdict(e) for e in self.edges]

        return DependencyGraphResult(
            nodes=nodes,
            edges=edge_dicts,
            cycles=cycles,
            symbol_index=symbol_index,
            file_count=len(nodes),
            edge_count=len(edge_dicts),
            cycle_count=len(cycles),
        )

    def _infer_language(self, path: str) -> str:
        ext = os.path.splitext(path)[1].lower()
        if ext == ".py":
            return "python"
        if ext in (".js", ".jsx", ".mjs", ".cjs"):
            return "javascript"
        if ext in (".ts", ".tsx"):
            return "typescript"
        return "plain"

    def _is_test_file(self, path: str) -> bool:
        parts = path.lower().split("/")
        return (
            "tests" in parts
            or "test" in parts
            or parts[-1].startswith("test_")
            or parts[-1].endswith("_test.py")
            or parts[-1].endswith(".test.ts")
            or parts[-1].endswith(".test.js")
        )

    def _parse_all_files(self) -> None:
        for path, content in self.files.items():
            lang = self._infer_language(path)
            if lang == "python":
                self._parse_python_file(path, content)
            else:
                self._parse_generic_file(path, content)

    def _parse_python_file(self, path: str, content: str) -> None:
        try:
            tree = ast.parse(content, filename=path)
        except SyntaxError:
            self._parse_generic_file(path, content)
            return

        class Visitor(ast.NodeVisitor):
            def __init__(self, builder: DependencyGraphBuilder):
                self.builder = builder
                self.current_class: str | None = None
                self.current_function: str | None = None

            def visit_Import(self, node: ast.Import):
                for alias in node.names:
                    self.builder.imports_by_file[path].append(
                        ImportRef(
                            source_file=path,
                            module=alias.name,
                            imported_name=alias.name,
                            alias=alias.asname,
                            line=node.lineno,
                            is_relative=False,
                            level=0,
                        )
                    )
                self.generic_visit(node)

            def visit_ImportFrom(self, node: ast.ImportFrom):
                module = node.module or ""
                level = node.level
                is_relative = level > 0
                for alias in node.names:
                    self.builder.imports_by_file[path].append(
                        ImportRef(
                            source_file=path,
                            module=module,
                            imported_name=alias.name,
                            alias=alias.asname,
                            line=node.lineno,
                            is_relative=is_relative,
                            level=level,
                        )
                    )
                self.generic_visit(node)

            def visit_ClassDef(self, node: ast.ClassDef):
                doc = ast.get_docstring(node)
                sym = SymbolDef(
                    name=node.name,
                    kind="class",
                    file_path=path,
                    line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                    docstring=doc,
                )
                self.builder.symbols_by_file[path].append(sym)
                self.builder.symbols_by_name[node.name].append(sym)

                prev_class = self.current_class
                self.current_class = node.name
                self.generic_visit(node)
                self.current_class = prev_class

            def _handle_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef):
                doc = ast.get_docstring(node)
                kind = "method" if self.current_class else "function"
                params = [arg.arg for arg in node.args.args]
                sym = SymbolDef(
                    name=node.name,
                    kind=kind,
                    file_path=path,
                    line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                    docstring=doc,
                    parent_class=self.current_class,
                    parameters=params,
                )
                self.builder.symbols_by_file[path].append(sym)
                self.builder.symbols_by_name[node.name].append(sym)

                prev_fn = self.current_function
                self.current_function = node.name
                self.generic_visit(node)
                self.current_function = prev_fn

            def visit_FunctionDef(self, node: ast.FunctionDef):
                self._handle_function(node)

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
                self._handle_function(node)

            def visit_Call(self, node: ast.Call):
                name: str | None = None
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    name = node.func.attr

                if name:
                    self.builder.calls_by_file[path].append(
                        CallRef(
                            source_file=path,
                            source_symbol=self.current_function or self.current_class,
                            called_name=name,
                            line=node.lineno,
                        )
                    )
                self.generic_visit(node)

        visitor = Visitor(self)
        visitor.visit(tree)

    def _parse_generic_file(self, path: str, content: str) -> None:
        """Lightweight regex parser for non-Python or syntax-error files."""
        for line_num, line in enumerate(content.splitlines(), 1):
            # JS/TS import: import { a, b } from './module'
            m_import = re.match(r"""import\s+.*?\s+from\s+['"]([^'"]+)['"]""", line)
            if m_import:
                mod = m_import.group(1)
                self.imports_by_file[path].append(
                    ImportRef(
                        source_file=path,
                        module=mod,
                        imported_name="*",
                        alias=None,
                        line=line_num,
                        is_relative=mod.startswith("."),
                        level=1 if mod.startswith(".") else 0,
                    )
                )

            # Function / Class definition
            m_fn = re.match(r"""(?:export\s+)?(?:async\s+)?function\s+([A-Za-z0-9_]+)\b""", line)
            if m_fn:
                name = m_fn.group(1)
                sym = SymbolDef(
                    name=name, kind="function", file_path=path, line=line_num, end_line=line_num
                )
                self.symbols_by_file[path].append(sym)
                self.symbols_by_name[name].append(sym)

            m_cls = re.match(r"""(?:export\s+)?class\s+([A-Za-z0-9_]+)\b""", line)
            if m_cls:
                name = m_cls.group(1)
                sym = SymbolDef(
                    name=name, kind="class", file_path=path, line=line_num, end_line=line_num
                )
                self.symbols_by_file[path].append(sym)
                self.symbols_by_name[name].append(sym)

    def _resolve_module_to_file(self, source_file: str, module: str, level: int) -> str | None:
        """Resolves module name to an existing indexed file path."""
        if not module and level == 0:
            return None

        source_dir = os.path.dirname(source_file)

        # 1. Relative import (e.g., from . import helper or from ..models import Order)
        if level > 0:
            curr_dir = source_dir
            for _ in range(level - 1):
                curr_dir = os.path.dirname(curr_dir)

            rel_parts = module.replace(".", "/").strip("/") if module else ""
            candidate_base = os.path.normpath(
                os.path.join(curr_dir, rel_parts) if rel_parts else curr_dir
            )
            candidates = [
                f"{candidate_base}.py",
                os.path.join(candidate_base, "__init__.py"),
                f"{candidate_base}.ts",
                f"{candidate_base}.js",
            ]
            for cand in candidates:
                cand_clean = cand.lstrip("/")
                if cand_clean in self.all_paths:
                    return cand_clean

        # 2. Absolute or package import (e.g. src.discount, calculator, models)
        mod_path = module.replace(".", "/")
        direct_candidates = [
            f"{mod_path}.py",
            f"{mod_path}/__init__.py",
            f"{mod_path}.ts",
            f"{mod_path}.js",
        ]
        for cand in direct_candidates:
            if cand in self.all_paths:
                return cand

        # Try prefixing with existing top-level directories in repo (e.g. src/, lib/, apps/)
        top_dirs = {p.split("/")[0] for p in self.all_paths if "/" in p}
        for top in top_dirs:
            cand = f"{top}/{mod_path}.py"
            if cand in self.all_paths:
                return cand
            cand_init = f"{top}/{mod_path}/__init__.py"
            if cand_init in self.all_paths:
                return cand_init

        # Try matching basename directly in repo
        base_mod = module.split(".")[-1]
        for p in self.all_paths:
            p_base = os.path.splitext(os.path.basename(p))[0]
            if p_base == base_mod and p != source_file:
                return p

        return None

    def _resolve_imports(self) -> None:
        for path, imports in self.imports_by_file.items():
            for imp in imports:
                target_file = self._resolve_module_to_file(path, imp.module, imp.level)
                if target_file and target_file in self.all_paths:
                    imp.target_file = target_file
                    # Check if imported symbol is defined in target
                    target_syms = {s.name for s in self.symbols_by_file.get(target_file, [])}
                    if imp.imported_name in target_syms or imp.imported_name == "*":
                        imp.relationship = "resolved"
                    else:
                        imp.relationship = "inferred"
                else:
                    imp.relationship = "unresolved"

    def _resolve_calls(self) -> None:
        for path, calls in self.calls_by_file.items():
            local_syms = {s.name: s for s in self.symbols_by_file.get(path, [])}
            imports_in_file = self.imports_by_file.get(path, [])

            for call in calls:
                # 1. Local call in same file
                if call.called_name in local_syms:
                    call.target_file = path
                    call.target_symbol = call.called_name
                    call.relationship = "resolved"
                    continue

                # 2. Imported symbol call
                resolved = False
                for imp in imports_in_file:
                    matches_import = (
                        imp.imported_name == call.called_name
                        or imp.alias == call.called_name
                        or imp.imported_name == "*"
                    )
                    if matches_import and imp.target_file:
                        call.target_file = imp.target_file
                        call.target_symbol = imp.imported_name
                        call.relationship = imp.relationship
                        resolved = True
                        break

                if not resolved:
                    # 3. Global lookup across repo symbols if unique
                    candidates = self.symbols_by_name.get(call.called_name, [])
                    if len(candidates) == 1 and candidates[0].file_path != path:
                        call.target_file = candidates[0].file_path
                        call.target_symbol = call.called_name
                        call.relationship = "inferred"
                    else:
                        call.relationship = "unresolved"

    def _build_edges(self) -> None:
        seen_edges: set[tuple[str, str, str, str | None, str | None, int]] = set()

        # Import Edges
        for path, imports in self.imports_by_file.items():
            for imp in imports:
                if imp.target_file and imp.target_file != path:
                    key = (
                        path,
                        imp.target_file,
                        "import",
                        None,
                        imp.imported_name,
                        imp.line,
                    )
                    if key not in seen_edges:
                        seen_edges.add(key)
                        self.edges.append(
                            DependencyEdge(
                                source=path,
                                target=imp.target_file,
                                kind="import",
                                relationship=imp.relationship,
                                line=imp.line,
                                target_symbol=imp.imported_name,
                                evidence=(
                                    f"{path}:{imp.line} imports '{imp.imported_name}' "
                                    f"from {imp.module}"
                                ),
                            )
                        )

        # Call Edges
        for path, calls in self.calls_by_file.items():
            for call in calls:
                if call.target_file and call.target_file != path:
                    key = (
                        path,
                        call.target_file,
                        "call",
                        call.source_symbol,
                        call.target_symbol,
                        call.line,
                    )
                    if key not in seen_edges:
                        seen_edges.add(key)
                        self.edges.append(
                            DependencyEdge(
                                source=path,
                                target=call.target_file,
                                kind="call",
                                relationship=call.relationship,
                                line=call.line,
                                source_symbol=call.source_symbol,
                                target_symbol=call.target_symbol,
                                evidence=(
                                    f"{path}:{call.line} calls '{call.called_name}()' "
                                    f"in {call.target_file}"
                                ),
                            )
                        )

    def detect_cycles(self) -> list[list[str]]:
        """Tarjan/DFS directed cycle detection across file dependencies."""
        adj: dict[str, set[str]] = defaultdict(set)
        for e in self.edges:
            if e.source != e.target:
                adj[e.source].add(e.target)

        visited: set[str] = set()
        rec_stack: list[str] = []
        in_stack: set[str] = set()
        cycles: list[list[str]] = []

        def dfs(node: str):
            visited.add(node)
            rec_stack.append(node)
            in_stack.add(node)

            for neighbor in adj.get(node, []):
                if neighbor not in visited:
                    dfs(neighbor)
                elif neighbor in in_stack:
                    # Found cycle
                    cycle_start = rec_stack.index(neighbor)
                    cycle = rec_stack[cycle_start:] + [neighbor]
                    cycles.append(cycle)

            rec_stack.pop()
            in_stack.remove(node)

        for path in self.all_paths:
            if path not in visited:
                dfs(path)

        return cycles

    def get_dependents(self, file_path: str) -> list[str]:
        """Return all files that directly depend on (import or call) file_path."""
        return sorted(
            {e.source for e in self.edges if e.target == file_path and e.source != file_path}
        )

    def get_dependencies(self, file_path: str) -> list[str]:
        """Return all files that file_path directly depends on."""
        return sorted(
            {e.target for e in self.edges if e.source == file_path and e.target != file_path}
        )

    def analyze_bug_impact(
        self,
        query: str,
        target_files: list[str] | None = None,
        target_symbols: list[str] | None = None,
        max_depth: int = 2,
    ) -> ImpactAnalysisResult:
        """Analyze potential cross-file bug blast radius and identify regression tests.

        Impact analysis is a static hypothesis based on repository AST and import dependencies;
        it identifies likely affected files and regression tests without replacing the complete
        test suite.
        """
        if not self._parsed:
            self.build()

        # 1. Identify Root Cause Candidates
        root_candidates: list[dict] = []
        primary_files: set[str] = set(target_files or [])
        primary_symbols: set[str] = set(target_symbols or [])

        # Extract potential symbol names from query
        words = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b", query)
        for word in words:
            if word in self.symbols_by_name:
                primary_symbols.add(word)
                for s in self.symbols_by_name[word]:
                    primary_files.add(s.file_path)

        # Match path mentions in query
        for path in self.all_paths:
            base = os.path.basename(path)
            if base in query or path in query:
                primary_files.add(path)

        # If still empty, fall back to non-test files
        if not primary_files:
            non_tests = [p for p in sorted(self.all_paths) if not self._is_test_file(p)]
            primary_files = set(non_tests[:2])

        for f in sorted(primary_files):
            symbols = [asdict(s) for s in self.symbols_by_file.get(f, [])]
            matched_syms = [
                s
                for s in symbols
                if s["name"] in primary_symbols or s["name"].lower() in query.lower()
            ]
            root_candidates.append(
                {
                    "file_path": f,
                    "matched_symbols": matched_syms or symbols[:3],
                    "confidence": "high" if matched_syms else "medium",
                    "reasoning": (
                        f"Identified as candidate root cause based on symbol matches in {f}."
                    ),
                }
            )

        # 2. Traverse Downstream Dependencies (Affected files & functions)
        affected_map: dict[str, dict] = {}
        queue: deque[tuple[str, int]] = deque((f, 0) for f in primary_files)
        visited_nodes = set(primary_files)

        while queue:
            curr_file, depth = queue.popleft()
            if depth >= max_depth:
                continue

            dependents = self.get_dependents(curr_file)
            for dep in dependents:
                # Find the edges connecting curr_file to dep
                incoming_edges = [
                    e for e in self.edges if e.source == dep and e.target == curr_file
                ]
                ev_str = "; ".join(e.evidence for e in incoming_edges[:3])

                if dep not in affected_map and dep not in primary_files:
                    affected_map[dep] = {
                        "file_path": dep,
                        "distance": depth + 1,
                        "relationship": incoming_edges[0].relationship
                        if incoming_edges
                        else "inferred",
                        "affected_by": curr_file,
                        "evidence": ev_str,
                        "is_test": self._is_test_file(dep),
                    }
                if dep not in visited_nodes:
                    visited_nodes.add(dep)
                    queue.append((dep, depth + 1))

        # 3. Identify Relevant Regression Tests
        suggested_tests: list[dict] = []
        all_affected_files = primary_files.union(affected_map.keys())

        for test_path in sorted(self.all_paths):
            if not self._is_test_file(test_path):
                continue

            # Direct import of affected file
            test_deps = set(self.get_dependencies(test_path))
            intersection = test_deps.intersection(all_affected_files)

            # Name-matching heuristic (e.g. test_calculator.py matches calculator.py)
            name_match = any(
                os.path.basename(af).split(".")[0] in os.path.basename(test_path)
                for af in all_affected_files
            )

            if intersection:
                ev_edges = [
                    e.evidence
                    for e in self.edges
                    if e.source == test_path and e.target in intersection
                ]
                suggested_tests.append(
                    {
                        "test_file": test_path,
                        "relevance": "high",
                        "reason": f"Directly tests {', '.join(sorted(intersection))}.",
                        "evidence": "; ".join(ev_edges[:2]),
                    }
                )
            elif name_match:
                suggested_tests.append(
                    {
                        "test_file": test_path,
                        "relevance": "medium",
                        "reason": "Filename matches affected component naming convention.",
                        "evidence": (
                            f"{test_path} targets "
                            f"{os.path.basename(test_path).replace('test_', '')}"
                        ),
                    }
                )

        # 4. Construct Subgraph for Visualization
        subgraph_files = all_affected_files.union(t["test_file"] for t in suggested_tests)
        sub_nodes = [
            asdict(
                FileNode(
                    path=p,
                    language=self._infer_language(p),
                    is_test=self._is_test_file(p),
                    outgoing_files=[
                        e.target for e in self.edges if e.source == p and e.target in subgraph_files
                    ],
                    incoming_files=[
                        e.source for e in self.edges if e.target == p and e.source in subgraph_files
                    ],
                )
            )
            for p in sorted(subgraph_files)
        ]
        sub_edges = [
            asdict(e)
            for e in self.edges
            if e.source in subgraph_files and e.target in subgraph_files
        ]

        disclaimer = (
            "Static Impact Hypothesis: Inferred dependency impact is based on static AST "
            "analysis and import relationships. Runtime execution paths may differ. "
            "AI-suggested tests do not replace executing the complete required regression "
            "test suite."
        )

        return ImpactAnalysisResult(
            query=query,
            root_cause_candidates=root_candidates,
            affected_dependencies=sorted(
                affected_map.values(), key=lambda x: (x["distance"], x["file_path"])
            ),
            suggested_tests=suggested_tests,
            dependency_subgraph={"nodes": sub_nodes, "edges": sub_edges},
            disclaimer=disclaimer,
        )
