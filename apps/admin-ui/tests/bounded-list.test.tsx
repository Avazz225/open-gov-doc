import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BoundedList, useListFilter } from "@/components/BoundedList";
import { I18nProvider } from "@/i18n";

// P91-S5: the shared pattern for long admin lists.

const NAMES = Array.from({ length: 15 }, (_, i) => `rolle-${i}`).concat(["Prüfer", "Archiv"]);

function Harness({ names }: { names: string[] }) {
  const { query, setQuery, filtered } = useListFilter(names, (n) => n);
  return (
    <BoundedList label="Rollen" total={names.length} shown={filtered.length} query={query} onQueryChange={setQuery}>
      <ul>
        {filtered.map((n) => (
          <li key={n}>{n}</li>
        ))}
      </ul>
    </BoundedList>
  );
}

describe("BoundedList", () => {
  it("offers a filter above the threshold, ignoring case and accents", () => {
    render(
      <I18nProvider>
        <Harness names={NAMES} />
      </I18nProvider>
    );
    expect(screen.getByRole("group", { name: "Rollen – Liste" })).toBeInTheDocument();
    const filter = screen.getByLabelText("Rollen filtern");
    fireEvent.change(filter, { target: { value: "PRUF" } });
    expect(screen.getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Prüfer"]);
    expect(screen.getByRole("status")).toHaveTextContent("1 von 17");
    fireEvent.change(filter, { target: { value: "nichts" } });
    expect(screen.getByText("Keine Treffer.")).toBeInTheDocument();
  });

  it("has no filter for short lists", () => {
    render(
      <I18nProvider>
        <Harness names={["a", "b"]} />
      </I18nProvider>
    );
    expect(screen.queryByLabelText("Rollen filtern")).not.toBeInTheDocument();
  });
});
