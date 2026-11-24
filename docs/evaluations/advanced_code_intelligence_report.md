# Advanced Code Intelligence & Impact Analysis Evaluation Report

## 1. Executive Summary

This report documents the design, implementation, and empirical verification of the **Advanced Code Intelligence & Impact Analysis Engine** in RepoPilot Fix.

RepoPilot Fix has been upgraded from file-level lexical and pattern-based retrieval to cross-file, AST-driven dependency intelligence. The upgraded architecture features:
1. **Static Dependency Graph Builder**: Analyzes AST structures across Python and multi-language projects to track file imports, class and function declarations, resolvable invocations, and reverse dependencies.
2. **Precision-Classified Cross-File Relationships**: Formally categorizes all symbol references and imports into `resolved` (confirmed target definition), `inferred` (unique matching repository symbol), and `unresolved` (external standard library or third-party dependency).
3. **Directed Cycle Detection**: Implements Tarjan/DFS recursion stack analysis to identify circular import loops (`has_circular_dependency`).
4. **Intelligent Context Selection**: Augments BM25 lexical ranking with AST symbol declaration bonuses, exact line window extraction, and context-dependent test file boosting for issue investigations.
5. **Static Bug Blast Radius & Impact Analysis**: Recursively traces caller/callee graphs to isolate likely root-cause files, downstream affected components, and suggested regression tests.
6. **Pure-SVG Visual Interface**: Interactive, lightweight dependency visualizer in the frontend with zero heavy graph libraries, conforming to the dark aesthetic with `#BFFF00` (neon lime) and `#00F0FF` (neon cyan) accents.
7. **Multi-File Deterministic Shop Fixture (`fixture_cross_file_shop`)**: Realistic multi-file pricing and discount repository with distractor modules and tests, verified with real isolated Docker container execution.

---

## 2. Graph Construction Methodology

### 2.1 AST Extraction
The engine utilizes Python's standard library `ast` module (complemented by regex fallback for polyglot files):
- **Class and Function Definitions**: Extracts symbol name, parameters, docstring, parent class, and exact start and end line ranges (`node.lineno` to `node.end_lineno`).
- **Import Statements**: Parses `ast.Import` and `ast.ImportFrom` nodes. Preserves relative import levels (`level > 0`) and module aliases (`asname`).
- **Call Sites**: Traverses `ast.Call` expressions, identifying direct callable names and attribute calls (`obj.method()`), associating each call site with its enclosing symbol and line number.

### 2.2 Module & Path Resolution
Relative and absolute imports are resolved against repository paths:
- Relative imports traverse upwards by `level - 1` directories from `os.path.dirname(source_file)`.
- Candidate file extensions (`.py`, `/__init__.py`, `.ts`, `.js`) are evaluated against indexed repository paths.
- Top-level repository prefixes (`src/`, `lib/`, `apps/`) are checked to support standard root package conventions.

---

## 3. Precision of Dependency Resolution

Relationships are classified into three mutually exclusive statuses:

| Relationship Status | Criteria | Example in `fixture_cross_file_shop` | Confidence Score |
| :--- | :--- | :--- | :--- |
| **`resolved`** | Import path points to an indexed file AND the imported symbol is declared in that target file. | `src/shop/pricing.py` imports `calculate_discount` from `src/shop/discounts.py`. | **1.0** (Deterministic) |
| **`inferred`** | Target file is resolved (e.g. `import *` or module import), or a unique symbol match exists across the repository without an explicit relative path. | Calling a uniquely named function defined elsewhere without a direct import. | **0.8** (Heuristic) |
| **`unresolved`** | External standard library (e.g., `import unittest`, `import os`), third-party dependency (e.g., `fastapi`, `pydantic`), or missing module. | `import unittest` in test files. | **0.5** (External/Unresolved) |

### Empirical Validation on `fixture_cross_file_shop`:
- **Total Files**: 10
- **Total Edges Detected**: 22
- **Resolved Edges**: 16 (100% of internal cross-file imports and symbol calls)
- **Inferred Edges**: 0
- **Unresolved Edges**: 6 (standard library `unittest` and `decimal` imports)
- **Cycle Count**: 0 (Clean DAG)

---

## 4. Directed Cycle Detection & Handling

Cycle detection runs DFS with an active recursion stack over the directed adjacency graph of file-to-file dependencies:
- **Clean DAGs**: Reports `cycle_count == 0` and `has_circular_dependency: False` on all nodes.
- **Circular Dependencies**: When an edge connects to a node already in the current recursion stack, the cycle path is extracted (`[Node_A, Node_B, Node_A]`), recorded in `summary.circular_chains`, and flagged on affected nodes.
- **Handling**: Graph traversal depth-limits prevent infinite recursion during impact analysis. Cycle files are styled with an amber alert indicator (`↻ cycle`) in the UI.

---

## 5. Retrieval Benchmark Comparison (Before vs. After)

Retrieval was evaluated against the standard repository benchmark suite (41 quality test cases in `apps/api/tests/test_retrieval_benchmark.py`) and the new multi-file cross-file shop fixture:

| Metric / Scenario | Before (Lexical BM25 Only) | After (Code Intelligence & Dependency Graph) | Improvement |
| :--- | :--- | :--- | :--- |
| **Standard Quality Benchmark (41 Cases)** | 41 / 41 Passed (100%) | 41 / 41 Passed (100%) | Maintained 100% precision with zero regression |
| **Exact Symbol Resolution Window** | Heuristic window around line matches (`line - 5` to `line + 5`) | Centered on AST symbol declaration (`start_line - 1` to `end_line + 2`) | Snippets fully enclose target function/class definitions |
| **Issue / Bug Query Test Relevance** | Test files penalized unconditionally (`score * 0.55`) | Test files importing suspect components boosted (bounded `score + 1.5` up to `max_source_score - 0.05`) on issue intent | Relevant regression tests surfaced in top 5 evidence |
| **Shop Fixture Root Cause Identification** | `src/shop/discounts.py` ranked among several mentions | `src/shop/discounts.py` ranked #1 with `calculate_discount` declaration | +5.0 declaration bonus on exact symbol match |

---

## 6. Bug Impact Analysis Accuracy & Limitations

### 6.1 Evaluation on `fixture_cross_file_shop`
- **Bug**: In `src/shop/discounts.py`, `calculate_discount()` returned `VIP_DISCOUNT_RATE` (0.20) instead of the calculated discount amount (`subtotal * VIP_DISCOUNT_RATE`).
- **Investigated Query**: `"vip discount calculation error in discounts.py"`
- **Result**:
  - **Root Cause Candidate**: `src/shop/discounts.py` (Confidence: High)
  - **Affected Downstream Dependencies**: `src/shop/pricing.py` (distance: 1), `src/shop/receipt.py` (distance: 2)
  - **Distractor Modules Excluded**: `src/shop/inventory.py` (Not Affected), `src/shop/shipping.py` (Not Affected)
  - **Suggested Regression Test**: `tests/test_pricing.py` (Confidence: High, directly imports and exercises `pricing.py`)
  - **Distractor Test Excluded**: `tests/test_shipping.py` (Correctly excluded from suggested tests)

### 6.2 Essential Limitations & Architectural Disclaimers

> [!IMPORTANT]
> **Static Impact Hypothesis**: Inferred dependency impact is based on static AST analysis and import relationships. Static analysis does not evaluate runtime dynamic dispatch, monkey-patching, reflection, or configuration-driven plugins.

> [!WARNING]
> **No Regression Suite Bypass**: AI-suggested regression tests serve as an immediate diagnostic aid for localized verification. Under no circumstances does RepoPilot Fix permit replacing or skipping the complete required regression test suite. All verified pull requests require explicit human approval and passing full sandbox verification.

---

## 7. Real Isolated Docker Verification

Real container verification was executed using `DockerSandboxRunner` with image `python:3.11-slim`:

1. **Baseline Execution**:
   - Command: `python -m unittest tests/test_pricing.py`
   - Outcome: **FAILED** (Exit code 1)
   - Failure: `AssertionError: 104.79 != 84.0` in `test_vip_customer_discount`.
2. **Approved Fix Patch Execution**:
   - Patch: `CROSS_FILE_SHOP_FIX_PATCH` applied to `src/shop/discounts.py`.
   - Command: `python -m unittest tests/test_pricing.py`
   - Outcome: **PASSED** (Exit code 0, 3/3 tests passed in 0.001s).
3. **Regression Detection Run**:
   - Patch: `CROSS_FILE_SHOP_REGRESSION_PATCH` applied (fixes VIP but alters standard customer discount).
   - Command: `python -m unittest tests/test_pricing.py`
   - Outcome: **FAILED** (Exit code 1, `AssertionError` in `test_standard_customer`).
   - Confirms that fixes causing collateral regressions cannot proceed.

> [!NOTE]
> **Evaluation Methodology**: The multi-file shop fixture was evaluated with real isolated Docker container execution against predefined deterministic reference patches (`CROSS_FILE_SHOP_FIX_PATCH` and `CROSS_FILE_SHOP_REGRESSION_PATCH`). It was verified through actual containerized execution (`python -m unittest tests/test_pricing.py` inside `python:3.11-slim`), confirming baseline failure, patch success, and regression rejection, rather than live AI generation or mock simulation.

---

## 8. Summary of Added Test Coverage

- `apps/api/tests/test_code_intelligence_evaluation.py`: 8 comprehensive tests (7 unit/integration + 1 real Docker test).
- `apps/web/src/lib/code-intelligence.test.ts`: Frontend type validation and structure unit tests.
- Total Backend Test Suite: 288 passed (+7 non-docker unit/integration), 10 skipped, 21 deselected (including 1 new Docker verification test for cross-file shop) = 319 total tests collected.
- Frontend Test Suite: 32 tests passed across 19 test suites.
