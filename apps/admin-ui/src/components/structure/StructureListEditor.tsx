"use client";

import { useId } from "react";
import { useI18n } from "@/i18n";

// One parent-side list (P91-S3, ADR 0255): "open" (null) or "only these"
// (a list, possibly empty). Used for a folder class's child folders and
// documents, and for the root's lists.
export function StructureListEditor({
  legend,
  value,
  options,
  onChange,
  checkbox,
}: {
  legend: string;
  value: string[] | null;
  options: string[];
  onChange: (next: string[] | null) => void;
  checkbox: string;
}) {
  const { t } = useI18n();
  const name = useId();
  const listed = value ?? [];
  // A stored name that is no longer a type stays visible until removed.
  const shown = [...options, ...listed.filter((n) => !options.includes(n))];

  function toggle(option: string) {
    onChange(listed.includes(option) ? listed.filter((n) => n !== option) : [...listed, option]);
  }

  return (
    <fieldset className="m-0 min-w-0 rounded-md border border-border p-3">
      <legend className="px-1 text-sm font-semibold">{legend}</legend>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
        <label className="flex items-center gap-2">
          <input
            type="radio"
            name={name}
            checked={value === null}
            onChange={() => onChange(null)}
          />
          {t("structure.listOpen")}
        </label>
        <label className="flex items-center gap-2">
          <input
            type="radio"
            name={name}
            checked={value !== null}
            onChange={() => onChange(listed)}
          />
          {t("structure.listOnly")}
        </label>
      </div>
      {value !== null && (
        <>
          {shown.length === 0 ? (
            <p className="mb-0 mt-2 text-sm italic opacity-70">{t("structure.noTypes")}</p>
          ) : (
            <div className="mt-2 flex max-h-48 flex-wrap gap-x-4 gap-y-1 overflow-y-auto text-sm">
              {shown.map((option) => (
                <label key={option} className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={listed.includes(option)}
                    onChange={() => toggle(option)}
                    className={checkbox}
                  />
                  {options.includes(option) ? option : t("structure.unknownType", { name: option })}
                </label>
              ))}
            </div>
          )}
          {listed.length === 0 && (
            <p className="mb-0 mt-2 text-sm opacity-80">{t("structure.listEmptyHint")}</p>
          )}
        </>
      )}
    </fieldset>
  );
}
