import { FlatCompat } from "@eslint/eslintrc";

const compat = new FlatCompat({ baseDirectory: import.meta.dirname });

const eslintConfig = [
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  {
    ignores: [".next/**", "out/**", "node_modules/**", "next-env.d.ts"],
  },
  {
    // Playwright's own fixture API conventionally names its callback
    // parameter `use` (`base.extend({ x: async ({}, use) => { ... } })`) -
    // not a React hook, but the react-hooks rule can't tell the difference
    // from the name alone. e2e/ isn't React code at all.
    files: ["e2e/**"],
    rules: {
      "react-hooks/rules-of-hooks": "off",
    },
  },
];

export default eslintConfig;
