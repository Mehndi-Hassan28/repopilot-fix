"""Benchmark dataset curating 20 open-source software defect benchmarks.

Each benchmark case records:
- Upstream project context and license provenance.
- Defect classification: logic_error, boundary_condition, input_validation,
  exception_handling, cross_file_dependency, multi_file_repair, regression_sensitive.
- Provenance classification:
  * defect_pattern_fixture: engineered standalone reproduction distilled from
    known open-source defect patterns.
  * original_historical: full upstream repository checkout at historical commit.
  * synthetic_fixture: engineered fixture designed for benchmark stress testing.
  (All 20 cases in this suite are defect-pattern fixtures modeling real bug patterns).
- Deterministic pattern reference identifier (pattern_ref).
- Reproducible codebase files and baseline test failure command.
- Sequestered reference fix patch for ground-truth verification (never exposed to AI prompt).
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

BenchmarkProvenance = Literal[
    "defect_pattern_fixture",
    "minimized_reproduction",
    "original_historical",
    "synthetic_fixture",
]

BenchmarkCategory = Literal[
    "logic_error",
    "boundary_condition",
    "input_validation",
    "exception_handling",
    "cross_file_dependency",
    "multi_file_repair",
    "regression_sensitive",
]

REQUIRED_CATEGORIES: list[BenchmarkCategory] = [
    "logic_error",
    "boundary_condition",
    "input_validation",
    "exception_handling",
    "cross_file_dependency",
    "multi_file_repair",
    "regression_sensitive",
]


@dataclass(frozen=True)
class BenchmarkIssue:
    id: str
    repo_name: str
    repo_url: str
    pattern_ref: str
    license: str
    category: BenchmarkCategory
    title: str
    description: str
    files: dict[str, str]
    test_command: str
    expected_baseline_error: str
    reference_patch: str
    target_file: str
    target_symbol: str
    reproduction_methodology: str
    is_multi_file: bool = False
    is_synthetic: bool = False
    provenance: BenchmarkProvenance = "defect_pattern_fixture"
    commit_sha: str | None = None
    issue_id: str | None = None


_DATASET_FILE = Path(__file__).parent / "dataset.json"

with open(_DATASET_FILE, encoding="utf-8") as _f:
    _raw_data = json.load(_f)

BENCHMARK_ISSUES: list[BenchmarkIssue] = [BenchmarkIssue(**item) for item in _raw_data]
BENCHMARK_BY_ID: dict[str, BenchmarkIssue] = {b.id: b for b in BENCHMARK_ISSUES}
