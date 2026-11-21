"use client";

import { useState } from "react";
import { useI18n } from "@/i18n";
import type { ObjectType } from "@/lib/api";

// Guided editors for an object type's rules (P88-S3, see
// docs/services/admin-ui.md "Object-Type Rules"): the naming rule, the
// conditional required fields and the status-transition rules. Before,
// `naming_constraints`/`conditions` were passed through raw and
// `status_transitions` were not sent at all - saving in the editor reset
// them. The shapes are exactly what `dms_constraint_engine` evaluates:
// - namingConstraints: {mustContain: [attr], pattern: "RE-{attr}-..."}
// - conditions: [{if: "<attr> <op> <value>", then: "require:<attr>"}]
// - statusTransitions: [{from, to, requiredAttributes: [attr]}]

export const CONDITION_OPERATORS = [">", ">=", "<", "<=", "==", "!="] as const;
export type ConditionOperator = (typeof CONDITION_OPERATORS)[number];

// Same pattern as `dms_constraint_engine.engine._CONDITION_RE`: the
// attribute name is a single word, so attributes with other characters
// can't be used in a condition (the engine couldn't read them).
const CONDITION_RE = /^\s*([A-Za-z0-9_äöüÄÖÜß]+)\s*(>=|<=|==|!=|>|<)\s*(.+?)\s*$/;
const CONDITION_ATTRIBUTE_RE = /^[A-Za-z0-9_äöüÄÖÜß]+$/;
const REQUIRE_PREFIX = "require:";

// Statuses an existing service actually checks today (case-service closes a
// case "open" -> "closed"); free text stays possible for future callers.
export const KNOWN_STATUSES = ["open", "closed"];

export interface ConditionDraft {
  attribute: string;
  operator: ConditionOperator;
  value: string;
  require: string;
  // A stored condition the guided form can't represent (written before
  // P88-S3's server-side check): kept unchanged, shown read-only.
  raw?: { if: unknown; then: unknown };
}

export interface TransitionDraft {
  from: string;
  to: string;
  requiredAttributes: string[];
}

export interface RulesDraft {
  mustContain: string[];
  pattern: string;
  conditions: ConditionDraft[];
  transitions: TransitionDraft[];
}

export function emptyRules(): RulesDraft {
  return { mustContain: [], pattern: "", conditions: [], transitions: [] };
}

export function parseConditionEntry(entry: Record<string, unknown>): ConditionDraft {
  const expression = typeof entry.if === "string" ? entry.if : "";
  const then = typeof entry.then === "string" ? entry.then : "";
  const match = CONDITION_RE.exec(expression);
  if (!match || !then.startsWith(REQUIRE_PREFIX)) {
    return { attribute: "", operator: ">", value: "", require: "", raw: { if: entry.if, then: entry.then } };
  }
  return {
    attribute: match[1],
    operator: match[2] as ConditionOperator,
    value: match[3],
    require: then.slice(REQUIRE_PREFIX.length).trim(),
  };
}

export function rulesFromObjectType(ot: ObjectType): RulesDraft {
  const naming = (ot.naming_constraints ?? {}) as { mustContain?: unknown; pattern?: unknown };
  return {
    mustContain: Array.isArray(naming.mustContain) ? naming.mustContain.map(String) : [],
    pattern: typeof naming.pattern === "string" ? naming.pattern : "",
    conditions: (ot.conditions ?? []).map(parseConditionEntry),
    transitions: (ot.status_transitions ?? []).map((tr) => ({
      from: tr.from,
      to: tr.to,
      requiredAttributes: tr.requiredAttributes ?? [],
    })),
  };
}

export function rulesToPayload(rules: RulesDraft) {
  const pattern = rules.pattern.trim();
  const namingConstraints =
    rules.mustContain.length > 0 || pattern
      ? {
          ...(rules.mustContain.length > 0 ? { mustContain: rules.mustContain } : {}),
          ...(pattern ? { pattern } : {}),
        }
      : null;
  return {
    namingConstraints,
    conditions: rules.conditions.map((c) =>
      c.raw
        ? (c.raw as Record<string, unknown>)
        : { if: `${c.attribute} ${c.operator} ${c.value.trim()}`, then: `${REQUIRE_PREFIX}${c.require}` }
    ),
    statusTransitions: rules.transitions.map((tr) => ({
      from: tr.from.trim(),
      to: tr.to.trim(),
      requiredAttributes: tr.requiredAttributes,
    })),
  };
}

// Client-side checks that mirror the server's (object-type-service
// `_validate_conditions`/`_validate_status_transitions`), so a mistake is
// named next to the form instead of as a raw 422 text. Returns an i18n key
// plus values, or null.
export function rulesError(
  rules: RulesDraft,
  attributeNames: string[]
): { key: string; vars?: Record<string, string | number> } | null {
  for (const [index, c] of rules.conditions.entries()) {
    if (c.raw) continue;
    if (!c.attribute || !c.value.trim() || !c.require) {
      return { key: "objectTypes.rules.conditionIncomplete", vars: { index: index + 1 } };
    }
  }
  const known = new Set(attributeNames);
  for (const [index, tr] of rules.transitions.entries()) {
    if (!tr.from.trim() || !tr.to.trim()) {
      return { key: "objectTypes.rules.transitionIncomplete", vars: { index: index + 1 } };
    }
    if (tr.from.trim() === tr.to.trim()) {
      return { key: "objectTypes.rules.transitionSame", vars: { index: index + 1 } };
    }
    const unknown = tr.requiredAttributes.find((name) => !known.has(name));
    if (unknown) {
      return { key: "objectTypes.rules.transitionUnknownAttribute", vars: { index: index + 1, name: unknown } };
    }
  }
  return null;
}

// The naming check of `dms_constraint_engine._validate_naming`, for the
// tester: the pattern is literal text with `{attribute}` placeholders; a
// placeholder matches any non-empty text here (the real check substitutes
// the document's value); the file extension is ignored.
export function nameMatchesPattern(pattern: string, name: string): boolean {
  const escaped = pattern.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const source = escaped.replace(/\\\{[A-Za-z0-9_äöüÄÖÜß]+\\\}/g, ".+");
  const stem = name.includes(".") ? name.slice(0, name.lastIndexOf(".")) : name;
  return new RegExp(`^(?:${source})$`).test(stem);
}

interface Props {
  rules: RulesDraft;
  onChange: (rules: RulesDraft) => void;
  attributeNames: string[];
  fieldInput: string;
  secondaryBtn: string;
  checkbox: string;
}

export function ObjectTypeRules({ rules, onChange, attributeNames, fieldInput, secondaryBtn, checkbox }: Props) {
  const { t } = useI18n();
  const [sampleName, setSampleName] = useState("");
  const conditionAttributes = attributeNames.filter((name) => CONDITION_ATTRIBUTE_RE.test(name));
  const known = new Set(attributeNames);

  function patch(next: Partial<RulesDraft>) {
    onChange({ ...rules, ...next });
  }

  function toggle(list: string[], name: string): string[] {
    return list.includes(name) ? list.filter((n) => n !== name) : [...list, name];
  }

  function updateCondition(index: number, next: Partial<ConditionDraft>) {
    patch({ conditions: rules.conditions.map((c, i) => (i === index ? { ...c, ...next } : c)) });
  }

  function updateTransition(index: number, next: Partial<TransitionDraft>) {
    patch({ transitions: rules.transitions.map((tr, i) => (i === index ? { ...tr, ...next } : tr)) });
  }

  // An attribute referenced by a rule but no longer declared (removed or
  // renamed above): still shown, marked, so the reference doesn't vanish
  // silently.
  const attributeOptions = (current: string, names: string[]) => (
    <>
      <option value="">{t("objectTypes.rules.chooseAttribute")}</option>
      {names.map((name) => (
        <option key={name} value={name}>
          {name}
        </option>
      ))}
      {current && !names.includes(current) && (
        <option value={current}>{t("objectTypes.rules.undeclared", { name: current })}</option>
      )}
    </>
  );

  const patternValue = rules.pattern.trim();
  const sampleResult = patternValue && sampleName.trim() ? nameMatchesPattern(patternValue, sampleName.trim()) : null;
  const undeclaredInPattern = [...patternValue.matchAll(/\{([^{}]+)\}/g)]
    .map((m) => m[1])
    .filter((name) => !known.has(name));

  return (
    <div className="object-type-rules">
      <h3>{t("objectTypes.rules.heading")}</h3>
      <p className="text-sm opacity-80">{t("objectTypes.rules.hint")}</p>

      <fieldset className="rounded-md border border-border p-3">
        <legend className="px-1 font-semibold">{t("objectTypes.rules.namingHeading")}</legend>
        <p className="mt-0 text-sm opacity-80">{t("objectTypes.rules.namingHint")}</p>
        <p className="mb-1 text-sm">{t("objectTypes.rules.mustContain")}</p>
        {attributeNames.length === 0 ? (
          <p className="text-sm italic opacity-70">{t("objectTypes.rules.noAttributesYet")}</p>
        ) : (
          <div className="checkbox-group">
            {[...attributeNames, ...rules.mustContain.filter((n) => !known.has(n))].map((name) => (
              <label key={name}>
                <input
                  type="checkbox"
                  checked={rules.mustContain.includes(name)}
                  onChange={() => patch({ mustContain: toggle(rules.mustContain, name) })}
                  className={checkbox}
                />
                {known.has(name) ? name : t("objectTypes.rules.undeclared", { name })}
              </label>
            ))}
          </div>
        )}
        <label className="mt-3 flex flex-col gap-1 text-sm">
          {t("objectTypes.rules.pattern")}
          <input
            value={rules.pattern}
            onChange={(e) => patch({ pattern: e.target.value })}
            placeholder={t("objectTypes.rules.patternPlaceholder")}
            className={`${fieldInput} max-w-md font-mono`}
          />
        </label>
        {attributeNames.length > 0 && (
          <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
            <span className="opacity-80">{t("objectTypes.rules.insertPlaceholder")}</span>
            {attributeNames.map((name) => (
              <button
                key={name}
                type="button"
                className={secondaryBtn}
                aria-label={t("objectTypes.rules.insertPlaceholderFor", { name })}
                onClick={() => patch({ pattern: `${rules.pattern}{${name}}` })}
              >
                {`{${name}}`}
              </button>
            ))}
          </div>
        )}
        {undeclaredInPattern.length > 0 && (
          <p className="text-sm text-danger">
            {t("objectTypes.rules.patternUndeclared", { names: undeclaredInPattern.join(", ") })}
          </p>
        )}
        {patternValue && (
          <div className="mt-2 flex flex-wrap items-end gap-2">
            <label className="flex flex-col gap-1 text-sm">
              {t("objectTypes.rules.sampleName")}
              <input
                value={sampleName}
                onChange={(e) => setSampleName(e.target.value)}
                className={`${fieldInput} max-w-md`}
              />
            </label>
            {sampleResult !== null && (
              <span className={`badge ${sampleResult ? "ok" : "down"}`} role="status">
                {sampleResult ? t("objectTypes.rules.sampleMatches") : t("objectTypes.rules.sampleFails")}
              </span>
            )}
          </div>
        )}
      </fieldset>

      <fieldset className="mt-3 rounded-md border border-border p-3">
        <legend className="px-1 font-semibold">{t("objectTypes.rules.conditionsHeading")}</legend>
        <p className="mt-0 text-sm opacity-80">{t("objectTypes.rules.conditionsHint")}</p>
        {rules.conditions.length === 0 && (
          <p className="text-sm italic opacity-70">{t("objectTypes.rules.noConditions")}</p>
        )}
        <ol className="m-0 flex list-none flex-col gap-2 p-0">
          {rules.conditions.map((condition, index) => (
            <li key={index} className="flex flex-wrap items-end gap-2" aria-label={t("objectTypes.rules.conditionLabel", { index: index + 1 })}>
              {condition.raw ? (
                <span className="text-sm">
                  {t("objectTypes.rules.rawCondition")}{" "}
                  <code className="font-mono text-xs">{JSON.stringify(condition.raw)}</code>
                </span>
              ) : (
                <>
                  <span className="pb-2 text-sm">{t("objectTypes.rules.if")}</span>
                  <label className="flex flex-col gap-1 text-sm">
                    {t("objectTypes.rules.attribute")}
                    <select
                      value={condition.attribute}
                      onChange={(e) => updateCondition(index, { attribute: e.target.value })}
                      className={fieldInput}
                    >
                      {attributeOptions(condition.attribute, conditionAttributes)}
                    </select>
                  </label>
                  <label className="flex flex-col gap-1 text-sm">
                    {t("objectTypes.rules.operator")}
                    <select
                      value={condition.operator}
                      onChange={(e) => updateCondition(index, { operator: e.target.value as ConditionOperator })}
                      className={fieldInput}
                    >
                      {CONDITION_OPERATORS.map((op) => (
                        <option key={op} value={op}>
                          {t(`objectTypes.rules.operators.${op}`)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="flex flex-col gap-1 text-sm">
                    {t("objectTypes.rules.value")}
                    <input
                      value={condition.value}
                      onChange={(e) => updateCondition(index, { value: e.target.value })}
                      className={`${fieldInput} w-32`}
                    />
                  </label>
                  <span className="pb-2 text-sm">{t("objectTypes.rules.thenRequired")}</span>
                  <label className="flex flex-col gap-1 text-sm">
                    {t("objectTypes.rules.requiredAttribute")}
                    <select
                      value={condition.require}
                      onChange={(e) => updateCondition(index, { require: e.target.value })}
                      className={fieldInput}
                    >
                      {attributeOptions(condition.require, attributeNames)}
                    </select>
                  </label>
                </>
              )}
              <button
                type="button"
                className={secondaryBtn}
                aria-label={t("objectTypes.rules.removeCondition", { index: index + 1 })}
                onClick={() => patch({ conditions: rules.conditions.filter((_, i) => i !== index) })}
              >
                ×
              </button>
            </li>
          ))}
        </ol>
        <button
          type="button"
          className={`${secondaryBtn} mt-2`}
          onClick={() =>
            patch({
              conditions: [...rules.conditions, { attribute: "", operator: ">", value: "", require: "" }],
            })
          }
        >
          {t("objectTypes.rules.addCondition")}
        </button>
      </fieldset>

      <fieldset className="mt-3 rounded-md border border-border p-3">
        <legend className="px-1 font-semibold">{t("objectTypes.rules.transitionsHeading")}</legend>
        <p className="mt-0 text-sm opacity-80">{t("objectTypes.rules.transitionsHint")}</p>
        <datalist id="object-type-known-statuses">
          {KNOWN_STATUSES.map((status) => (
            <option key={status} value={status} />
          ))}
        </datalist>
        {rules.transitions.length === 0 && (
          <p className="text-sm italic opacity-70">{t("objectTypes.rules.noTransitions")}</p>
        )}
        <ol className="m-0 flex list-none flex-col gap-3 p-0">
          {rules.transitions.map((transition, index) => (
            <li key={index} className="rounded-md bg-hover-bg p-2" aria-label={t("objectTypes.rules.transitionLabel", { index: index + 1 })}>
              <div className="flex flex-wrap items-end gap-2">
                <label className="flex flex-col gap-1 text-sm">
                  {t("objectTypes.rules.from")}
                  <input
                    list="object-type-known-statuses"
                    value={transition.from}
                    onChange={(e) => updateTransition(index, { from: e.target.value })}
                    className={`${fieldInput} w-36`}
                  />
                </label>
                <span className="pb-2" aria-hidden="true">
                  →
                </span>
                <label className="flex flex-col gap-1 text-sm">
                  {t("objectTypes.rules.to")}
                  <input
                    list="object-type-known-statuses"
                    value={transition.to}
                    onChange={(e) => updateTransition(index, { to: e.target.value })}
                    className={`${fieldInput} w-36`}
                  />
                </label>
                <button
                  type="button"
                  className={secondaryBtn}
                  aria-label={t("objectTypes.rules.removeTransition", { index: index + 1 })}
                  onClick={() => patch({ transitions: rules.transitions.filter((_, i) => i !== index) })}
                >
                  ×
                </button>
              </div>
              <p className="mb-1 mt-2 text-sm">{t("objectTypes.rules.transitionRequires")}</p>
              {attributeNames.length === 0 ? (
                <p className="m-0 text-sm italic opacity-70">{t("objectTypes.rules.noAttributesYet")}</p>
              ) : (
                <div className="checkbox-group">
                  {[...attributeNames, ...transition.requiredAttributes.filter((n) => !known.has(n))].map((name) => (
                    <label key={name}>
                      <input
                        type="checkbox"
                        checked={transition.requiredAttributes.includes(name)}
                        onChange={() =>
                          updateTransition(index, { requiredAttributes: toggle(transition.requiredAttributes, name) })
                        }
                        className={checkbox}
                      />
                      {known.has(name) ? name : t("objectTypes.rules.undeclared", { name })}
                    </label>
                  ))}
                </div>
              )}
            </li>
          ))}
        </ol>
        <button
          type="button"
          className={`${secondaryBtn} mt-2`}
          onClick={() => patch({ transitions: [...rules.transitions, { from: "", to: "", requiredAttributes: [] }] })}
        >
          {t("objectTypes.rules.addTransition")}
        </button>
      </fieldset>
    </div>
  );
}
