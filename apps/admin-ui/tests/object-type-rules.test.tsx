import { fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import {
  ObjectTypeRules,
  emptyRules,
  nameMatchesPattern,
  parseConditionEntry,
  rulesError,
  rulesToPayload,
  type RulesDraft,
} from "@/components/ObjectTypeRules";
import { I18nProvider } from "@/i18n";

// P88-S3: guided editors for naming rule, conditions and status transitions.

function Harness({ initial, attributes, onRules }: { initial: RulesDraft; attributes: string[]; onRules: (r: RulesDraft) => void }) {
  const [rules, setRules] = useState(initial);
  return (
    <ObjectTypeRules
      rules={rules}
      onChange={(next) => {
        setRules(next);
        onRules(next);
      }}
      attributeNames={attributes}
      fieldInput=""
      secondaryBtn=""
      checkbox=""
    />
  );
}

function renderRules(initial: RulesDraft = emptyRules(), attributes = ["Rechnungsnummer", "Betrag", "Kostenstelle"]) {
  let latest = initial;
  render(
    <I18nProvider>
      <Harness initial={initial} attributes={attributes} onRules={(r) => (latest = r)} />
    </I18nProvider>
  );
  return () => latest;
}

describe("ObjectTypeRules", () => {
  it("builds a naming rule from checkboxes and placeholder buttons and tests a sample name", () => {
    const current = renderRules();
    fireEvent.click(screen.getAllByLabelText("Rechnungsnummer")[0]);
    fireEvent.change(screen.getByLabelText("Namensmuster"), { target: { value: "RE-" } });
    fireEvent.click(screen.getByRole("button", { name: "Platzhalter Rechnungsnummer einfügen" }));

    expect(rulesToPayload(current()).namingConstraints).toEqual({
      mustContain: ["Rechnungsnummer"],
      pattern: "RE-{Rechnungsnummer}",
    });
    fireEvent.change(screen.getByLabelText("Beispielname prüfen"), { target: { value: "RE-000123.pdf" } });
    expect(screen.getByRole("status")).toHaveTextContent("Passt zum Muster");
    fireEvent.change(screen.getByLabelText("Beispielname prüfen"), { target: { value: "Rechnung 123.pdf" } });
    expect(screen.getByRole("status")).toHaveTextContent("Passt nicht zum Muster");
  });

  it("builds a condition as a sentence and serializes it in the engine's shape", () => {
    const current = renderRules();
    fireEvent.click(screen.getByRole("button", { name: "Bedingung hinzufügen" }));
    const row = screen.getByRole("listitem", { name: "Bedingung 1" });
    fireEvent.change(within(row).getByLabelText("Attribut"), { target: { value: "Betrag" } });
    fireEvent.change(within(row).getByLabelText("Vergleich"), { target: { value: ">=" } });
    fireEvent.change(within(row).getByLabelText("Wert"), { target: { value: "10000" } });
    fireEvent.change(within(row).getByLabelText("Pflichtattribut"), { target: { value: "Kostenstelle" } });

    expect(rulesToPayload(current()).conditions).toEqual([
      { if: "Betrag >= 10000", then: "require:Kostenstelle" },
    ]);
    expect(rulesError(current(), ["Betrag", "Kostenstelle"])).toBeNull();
  });

  it("keeps an unparseable stored condition unchanged and read-only", () => {
    const raw = { if: "Betrag ist hoch", then: "require:X" };
    const initial = { ...emptyRules(), conditions: [parseConditionEntry(raw)] };
    const current = renderRules(initial);
    expect(screen.getByText(/Nicht geführt darstellbar/)).toBeInTheDocument();
    expect(rulesToPayload(current()).conditions).toEqual([raw]);
  });

  it("marks references to undeclared attributes instead of dropping them", () => {
    const initial = {
      ...emptyRules(),
      conditions: [parseConditionEntry({ if: "Betrag > 1", then: "require:Kostenstelle" })],
    };
    renderRules(initial, ["Betrag"]);
    expect(screen.getByRole("option", { name: "Kostenstelle (nicht deklariert)" })).toBeInTheDocument();
  });

  it("edits a status transition with suggested statuses and required attributes", () => {
    const current = renderRules();
    fireEvent.click(screen.getByRole("button", { name: "Statusübergang hinzufügen" }));
    const row = screen.getByRole("listitem", { name: "Statusübergang 1" });
    fireEvent.change(within(row).getByLabelText("Von Status"), { target: { value: "open" } });
    fireEvent.change(within(row).getByLabelText("Nach Status"), { target: { value: "closed" } });
    fireEvent.click(within(row).getByLabelText("Kostenstelle"));

    expect(rulesToPayload(current()).statusTransitions).toEqual([
      { from: "open", to: "closed", requiredAttributes: ["Kostenstelle"] },
    ]);
    expect(document.querySelectorAll("#object-type-known-statuses option")).toHaveLength(2);
  });

  it("names incomplete or contradictory rules before the server would", () => {
    const transition = (from: string, to: string, requiredAttributes: string[] = []) => ({
      ...emptyRules(),
      transitions: [{ from, to, requiredAttributes }],
    });
    expect(rulesError(transition("", "closed"), [])?.key).toBe("objectTypes.rules.transitionIncomplete");
    expect(rulesError(transition("open", "open"), [])?.key).toBe("objectTypes.rules.transitionSame");
    expect(rulesError(transition("open", "closed", ["Weg"]), ["Betrag"])).toEqual({
      key: "objectTypes.rules.transitionUnknownAttribute",
      vars: { index: 1, name: "Weg" },
    });
    const halfCondition = {
      ...emptyRules(),
      conditions: [{ attribute: "Betrag", operator: ">" as const, value: "", require: "" }],
    };
    expect(rulesError(halfCondition, ["Betrag"])?.key).toBe("objectTypes.rules.conditionIncomplete");
  });

  it("matches names like the constraint engine: literal text, placeholders, extension ignored", () => {
    expect(nameMatchesPattern("RE-{Nr}", "RE-42.pdf")).toBe(true);
    expect(nameMatchesPattern("RE-{Nr}", "RE-.pdf")).toBe(false);
    expect(nameMatchesPattern("a.b-{Nr}", "a.b-1.txt")).toBe(true);
    expect(nameMatchesPattern("a.b-{Nr}", "axb-1.txt")).toBe(false);
    expect(nameMatchesPattern("(x)", "(x)")).toBe(true);
  });

  it("drops an empty naming rule instead of storing an empty object", () => {
    expect(rulesToPayload({ ...emptyRules(), pattern: "  " }).namingConstraints).toBeNull();
  });
});
