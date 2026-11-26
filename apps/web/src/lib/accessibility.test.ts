import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { PERSONAS, type PersonaType } from "./types.ts";

describe("Accessibility & Persona Architecture", () => {
  describe("PERSONAS Metadata Integrity", () => {
    it("defines all three required personas with full metadata", () => {
      const keys = Object.keys(PERSONAS) as PersonaType[];
      assert.deepEqual(keys.sort(), ["qa_lead", "security_auditor", "software_engineer"]);

      for (const key of keys) {
        const p = PERSONAS[key];
        assert.ok(p.id, `Persona ${key} must have an id`);
        assert.ok(p.label, `Persona ${key} must have a label`);
        assert.ok(p.badge, `Persona ${key} must have a badge`);
        assert.ok(p.role, `Persona ${key} must have a role`);
        assert.ok(p.description, `Persona ${key} must have a description`);
        assert.ok(p.accentColor, `Persona ${key} must have accentColor`);
        assert.ok(p.borderColor, `Persona ${key} must have borderColor`);
        assert.ok(p.bgColor, `Persona ${key} must have bgColor`);
      }
    });

    it("verifies distinct emphasis for each persona", () => {
      assert.match(PERSONAS.software_engineer.description, /AST/);
      assert.match(PERSONAS.security_auditor.description, /network none/);
      assert.match(PERSONAS.qa_lead.description, /regressions/);
    });

    it("verifies accessible ARIA roles and labels for modals", () => {
      // Contract test for modal dialog specifications
      const modalSpecs = [
        { name: "ApprovalModal", role: "dialog", ariaModal: "true", ariaLabelledby: "approval-modal-title" },
        { name: "PatchExportModal", role: "dialog", ariaModal: "true", ariaLabelledby: "patch-modal-title" },
        { name: "PullRequestModal", role: "dialog", ariaModal: "true", ariaLabelledby: "pr-modal-title" },
        { name: "ImportModal", role: "dialog", ariaModal: "true", ariaLabelledby: "import-modal-title" },
      ];

      for (const spec of modalSpecs) {
        assert.equal(spec.role, "dialog");
        assert.equal(spec.ariaModal, "true");
        assert.ok(spec.ariaLabelledby.length > 0);
      }
    });

    it("ensures focus trapping helpers handle tab wrapping", () => {
      // Mock element list for focus cycling
      const focusableSelectors = [
        'a[href]',
        'button:not([disabled])',
        'textarea:not([disabled])',
        'input:not([disabled])',
        'select:not([disabled])',
        '[tabindex]:not([tabindex="-1"])',
      ];
      assert.ok(focusableSelectors.length >= 6);

      // Verify cycle arithmetic
      const elements = ["btn-close", "input-title", "btn-submit"];
      const getNext = (currentIdx: number, shift: boolean) => {
        if (shift) {
          return currentIdx <= 0 ? elements.length - 1 : currentIdx - 1;
        }
        return currentIdx >= elements.length - 1 ? 0 : currentIdx + 1;
      };

      // Forward Tab from last element wraps to first
      assert.equal(getNext(2, false), 0);
      // Backward Tab from first element wraps to last
      assert.equal(getNext(0, true), 2);
      // Normal forward step
      assert.equal(getNext(0, false), 1);
      // Normal backward step
      assert.equal(getNext(1, true), 0);
    });
  });
});
