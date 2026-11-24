import { describe, it } from "node:test";
import assert from "node:assert/strict";
import type { DependencyGraphData, ImpactAnalysisData } from "./types";

describe("Code Intelligence Data Structures", () => {
  it("validates dependency graph structure", () => {
    const mockGraph: DependencyGraphData = {
      repository_id: 1,
      commit_sha: "a".repeat(40),
      summary: {
        total_files: 3,
        total_symbols: 6,
        total_edges: 2,
        resolved_edges: 2,
        inferred_edges: 0,
        unresolved_edges: 0,
        has_circular_dependency: false,
        circular_chains: [],
      },
      nodes: [
        {
          path: "src/shop/pricing.py",
          language: "python",
          functions: ["compute_final_price"],
          classes: [],
          imports: ["calculate_discount"],
          exports: ["compute_final_price"],
          calls: ["src/shop/discounts.py"],
          outgoing_files: ["src/shop/discounts.py"],
          incoming_files: [],
          has_circular_dependency: false,
        },
      ],
      edges: [
        {
          source: "src/shop/pricing.py",
          target: "src/shop/discounts.py",
          kind: "import",
          symbol: "calculate_discount",
          status: "resolved",
          confidence: 1.0,
        },
      ],
    };

    assert.equal(mockGraph.summary.total_files, 3);
    assert.equal(mockGraph.summary.resolved_edges, 2);
    assert.equal(mockGraph.summary.has_circular_dependency, false);
    assert.equal(mockGraph.nodes[0].path, "src/shop/pricing.py");
    assert.equal(mockGraph.edges[0].status, "resolved");
  });

  it("validates bug impact analysis structure and disclaimer", () => {
    const mockImpact: ImpactAnalysisData = {
      repository_id: 1,
      commit_sha: "b".repeat(40),
      likely_root_cause_files: ["src/shop/discounts.py"],
      affected_downstream_files: ["src/shop/pricing.py", "src/shop/receipt.py"],
      suggested_regression_tests: ["tests/test_pricing.py"],
      relations: [
        {
          target: "src/shop/pricing.py",
          relation: "imported_by",
          confidence: 0.9,
          status: "resolved",
          evidence_lines: [9],
          explanation: "Directly affected by src/shop/discounts.py",
        },
      ],
      disclaimer: "Static Impact Hypothesis: Inferred dependency impact is based on static AST analysis.",
    };

    assert.equal(mockImpact.likely_root_cause_files[0], "src/shop/discounts.py");
    assert.equal(mockImpact.affected_downstream_files.length, 2);
    assert.equal(mockImpact.suggested_regression_tests[0], "tests/test_pricing.py");
    assert.ok(mockImpact.disclaimer.includes("Static Impact Hypothesis"));
  });
});
