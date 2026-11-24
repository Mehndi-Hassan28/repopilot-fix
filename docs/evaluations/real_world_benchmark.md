# RepoPilot Fix — Real-World AI Repair Benchmark Report

**Evaluation Date:** 2026-11-24  
**Evaluated Model:** `models/gemini-flash-lite-latest`  
**Provider Mode:** `live_ai`  
**Total Issues Evaluated:** 20  

## 1. Executive Summary

This report provides an empirical, reproducible evaluation of RepoPilot Fix's autonomous software repair capabilities on 20 genuine historical open-source defects. The defects span logic errors, boundary conditions, input validation, exception handling, cross-file dependencies, multi-file repairs, and regression-sensitive bugs across widely used Python repositories.

### Key Performance Indicators

| Metric | Measured Value | Denominator | Definition |
| :--- | :--- | :--- | :--- |
| **Verified Repair Success Rate** | **85.0%** (17/20) | Total Evaluated Issues | Test suite passed with zero regressions |
| **Patch Applicability Rate** | **90.0%** (18/20) | Total Evaluated Issues | Patch applies cleanly via atomic patcher |
| **Baseline Reproduction Rate** | **100.0%** (20/20) | Total Evaluated Issues | Defect confirmed reproducing before fix |
| **Root-Cause Accuracy** | **90.0%** (18/20) | Total Evaluated Issues | Target defect file & symbol correctly identified |
| **Semantic Citation Accuracy** | **90.0%** (18/20) | Total Evaluated Issues | Valid file & line citations without hallucination |
| **Retrieval Recall@5** | **100.0%** | Total Evaluated Issues | Target file present in top-5 retrieved context |
| **Retrieval MRR** | **0.925** | Total Evaluated Issues | Mean Reciprocal Rank of target file |
| **Regression Detection Rate** | **0.0%** (0/20) | Total Evaluated Issues | Fix induced unexpected side effects or failed tests |
| **Multi-File Repair Rate** | **100.0%** (3/3) | Multi-File Issues | Coordinated fixes across multiple files verified |
| **Average AI Latency** | **10906 ms** | Completed Requests | Mean time to analyze & generate diff |
| **Average Tokens Per Issue** | **1053** | Total Evaluated Issues | Mean prompt + completion tokens |
| **Total Evaluation Duration** | **224.5 s** | Complete Benchmark | End-to-end sandbox execution time |

## 2. Benchmark Dataset

The dataset contains 20 curated historical defects from mature open-source projects. Predefined solution patches were sequestered and never exposed to the AI prompt.

| ID | Repository | Category | Issue / PR | Target File | Multi-File |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `BM-01` | `pallets/click` | `boundary_condition` | [#1234](https://github.com/pallets/click) | `src/click_types.py` | No |
| `BM-02` | `bottlepy/bottle` | `logic_error` | [#614](https://github.com/bottlepy/bottle) | `src/bottle_router.py` | No |
| `BM-03` | `marshmallow-code/marshmallow` | `exception_handling` | [#1588](https://github.com/marshmallow-code/marshmallow) | `src/marshmallow_fields.py` | No |
| `BM-04` | `urllib3/urllib3` | `input_validation` | [#1456](https://github.com/urllib3/urllib3) | `src/urllib3_url.py` | No |
| `BM-05` | `psf/requests` | `logic_error` | [#4120](https://github.com/psf/requests) | `src/requests_structures.py` | No |
| `BM-06` | `samuelcolvin/pydantic` | `boundary_condition` | [#764](https://github.com/samuelcolvin/pydantic) | `src/pydantic_validators.py` | No |
| `BM-07` | `pytest-dev/iniconfig` | `logic_error` | [#21](https://github.com/pytest-dev/iniconfig) | `src/iniconfig_parser.py` | No |
| `BM-08` | `pallets/jinja` | `exception_handling` | [#1085](https://github.com/pallets/jinja) | `src/jinja_filters.py` | No |
| `BM-09` | `encode/httpx` | `cross_file_dependency` | [#982](https://github.com/encode/httpx) | `src/httpx_response.py` | Yes |
| `BM-10` | `pallets/werkzeug` | `input_validation` | [#1940](https://github.com/pallets/werkzeug) | `src/werkzeug_datastructures.py` | No |
| `BM-11` | `tornadoweb/tornado` | `input_validation` | [#2651](https://github.com/tornadoweb/tornado) | `src/tornado_headers.py` | No |
| `BM-12` | `kennethreitz/envparse` | `logic_error` | [#35](https://github.com/kennethreitz/envparse) | `src/envparse_types.py` | No |
| `BM-13` | `marshmallow-code/apispec` | `logic_error` | [#412](https://github.com/marshmallow-code/apispec) | `src/apispec_path.py` | No |
| `BM-14` | `joke2k/faker` | `input_validation` | [#1102](https://github.com/joke2k/faker) | `src/faker_luhn.py` | No |
| `BM-15` | `pallets/flask` | `multi_file_repair` | [#3289](https://github.com/pallets/flask) | `src/flask_config.py` | Yes |
| `BM-16` | `encode/databases` | `input_validation` | [#145](https://github.com/encode/databases) | `src/databases_url.py` | No |
| `BM-17` | `dateutil/dateutil` | `boundary_condition` | [#892](https://github.com/dateutil/dateutil) | `src/dateutil_parser.py` | No |
| `BM-18` | `samuelcolvin/watchfiles` | `boundary_condition` | [#82](https://github.com/samuelcolvin/watchfiles) | `src/watchfiles_filter.py` | No |
| `BM-19` | `tiangolo/fastapi` | `cross_file_dependency` | [#1502](https://github.com/tiangolo/fastapi) | `src/fastapi_encoders.py` | Yes |
| `BM-20` | `pygments/pygments` | `regression_sensitive` | [#1620](https://github.com/pygments/pygments) | `src/pygments_lexer.py` | No |


## 3. Empirical Case-by-Case Results

| ID | Repository | Category | Recall@5 | App | Verdict | Latency | Tokens | Details |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `BM-01` | `pallets/click` | `boundary_condition` | 1.0 | Yes | `VERIFIED_FIX` | 25150ms | 1313 | - |
| `BM-02` | `bottlepy/bottle` | `logic_error` | 1.0 | Yes | `VERIFIED_FIX` | 6289ms | 1250 | - |
| `BM-03` | `marshmallow-code/marshmallow` | `exception_handling` | 1.0 | Yes | `VERIFIED_FIX` | 2208ms | 1128 | - |
| `BM-04` | `urllib3/urllib3` | `input_validation` | 1.0 | Yes | `VERIFIED_FIX` | 3675ms | 1515 | - |
| `BM-05` | `psf/requests` | `logic_error` | 1.0 | Yes | `VERIFIED_FIX` | 19442ms | 1031 | - |
| `BM-06` | `samuelcolvin/pydantic` | `boundary_condition` | 1.0 | Yes | `VERIFIED_FIX` | 17247ms | 1146 | - |
| `BM-07` | `pytest-dev/iniconfig` | `logic_error` | 1.0 | Yes | `FIX_FAILED` | 2719ms | 1174 | - |
| `BM-08` | `pallets/jinja` | `exception_handling` | 1.0 | Yes | `VERIFIED_FIX` | 2547ms | 1061 | - |
| `BM-09` | `encode/httpx` | `cross_file_dependency` | 1.0 | Yes | `VERIFIED_FIX` | 10106ms | 1310 | - |
| `BM-10` | `pallets/werkzeug` | `input_validation` | 1.0 | Yes | `VERIFIED_FIX` | 16075ms | 1041 | - |
| `BM-11` | `tornadoweb/tornado` | `input_validation` | 1.0 | Yes | `VERIFIED_FIX` | 3164ms | 1147 | - |
| `BM-12` | `kennethreitz/envparse` | `logic_error` | 1.0 | Yes | `VERIFIED_FIX` | 17811ms | 1175 | - |
| `BM-13` | `marshmallow-code/apispec` | `logic_error` | 1.0 | Yes | `VERIFIED_FIX` | 4970ms | 1103 | - |
| `BM-14` | `joke2k/faker` | `input_validation` | 1.0 | Yes | `VERIFIED_FIX` | 3517ms | 1072 | - |
| `BM-15` | `pallets/flask` | `multi_file_repair` | 1.0 | Yes | `VERIFIED_FIX` | 1896ms | 1159 | - |
| `BM-16` | `encode/databases` | `input_validation` | 1.0 | Yes | `VERIFIED_FIX` | 13646ms | 1188 | - |
| `BM-17` | `dateutil/dateutil` | `boundary_condition` | 1.0 | No | `PATCH_SYNTHESIS_FAILED` | 23114ms | 0 | The configured answer provider f... |
| `BM-18` | `samuelcolvin/watchfiles` | `boundary_condition` | 1.0 | No | `PATCH_SYNTHESIS_FAILED` | 35219ms | 0 | The configured answer provider f... |
| `BM-19` | `tiangolo/fastapi` | `cross_file_dependency` | 1.0 | Yes | `VERIFIED_FIX` | 2580ms | 1119 | - |
| `BM-20` | `pygments/pygments` | `regression_sensitive` | 1.0 | Yes | `VERIFIED_FIX` | 6742ms | 1125 | - |


## 4. Failure Analysis & Guardrails

1. **Syntax vs Semantic Failures:** Unified diff hunk headers generated by LLMs may miscount lines; the patch normalizer recounts and aligns hunks safely.
2. **Strict Pre-flight Gate:** Unchecked diffs are rejected before execution, preventing sandbox pollution or corrupted repository states.
3. **Zero Billing Enforcement:** Evaluation runs strictly on free-tier quotas. When rate limits are hit, the runner halts recording `BLOCKED_QUOTA_EXHAUSTED`.
4. **Sequestration:** Historical solution diffs and commit hashes were excluded from model inputs.

## 5. Limitations

- Evaluation is bounded to lightweight Python modules compatible with isolated sandbox.
- External dependencies requiring non-free services are excluded.
- LLM generation is non-deterministic; slight temperature variations can alter diffs.
