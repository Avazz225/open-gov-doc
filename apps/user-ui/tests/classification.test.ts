import { describe, expect, it } from "vitest";
import { classificationRank, raisableClassificationLevels } from "@/lib/classification";

describe("classificationRank", () => {
  it("ranks null (unclassified) as 0", () => {
    expect(classificationRank(null)).toBe(0);
  });

  it("ranks the four named levels in ascending order", () => {
    expect(classificationRank("VS-NfD")).toBe(1);
    expect(classificationRank("VS-VERTRAULICH")).toBe(2);
    expect(classificationRank("GEHEIM")).toBe(3);
    expect(classificationRank("STRENG GEHEIM")).toBe(4);
  });

  it("treats an unknown string as unclassified rank 0", () => {
    expect(classificationRank("not-a-real-level")).toBe(0);
  });
});

describe("raisableClassificationLevels", () => {
  it("offers all four levels from unclassified", () => {
    expect(raisableClassificationLevels(null)).toEqual([
      "VS-NfD",
      "VS-VERTRAULICH",
      "GEHEIM",
      "STRENG GEHEIM",
    ]);
  });

  it("excludes lower levels once a level is already set", () => {
    expect(raisableClassificationLevels("GEHEIM")).toEqual(["GEHEIM", "STRENG GEHEIM"]);
  });

  it("includes the current level itself (idempotent raise)", () => {
    expect(raisableClassificationLevels("VS-NfD")).toContain("VS-NfD");
  });

  it("offers nothing beyond the current level once at the highest rank", () => {
    expect(raisableClassificationLevels("STRENG GEHEIM")).toEqual(["STRENG GEHEIM"]);
  });
});
