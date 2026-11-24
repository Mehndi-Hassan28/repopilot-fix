"""Benchmark dataset curating 20 genuine historical open-source software defects.

Each benchmark case records:
- Original repository URL, issue reference, pinned commit SHA, and license.
- Reproducible codebase files and baseline test failure.
- Reference fix patch for ground-truth verification (never exposed to AI prompt).
- Classification: logic_error, boundary_condition, input_validation, exception_handling,
  cross_file_dependency, multi_file_repair, regression_sensitive.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

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
    commit_sha: str
    issue_id: str
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


_DATASET_FILE = Path(__file__).parent / "dataset.json"

with open(_DATASET_FILE, encoding="utf-8") as _f:
    _raw_data = json.load(_f)

BENCHMARK_ISSUES: list[BenchmarkIssue] = [BenchmarkIssue(**item) for item in _raw_data]
BENCHMARK_BY_ID: dict[str, BenchmarkIssue] = {b.id: b for b in BENCHMARK_ISSUES}
