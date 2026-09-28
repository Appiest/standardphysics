import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    rules: {
      complexity: ["error", { max: 8 }],
    },
  },
  globalIgnores([".next/**", ".notebooks/**", "out/**", "build/**", "next-env.d.ts", "src/types/contracts.ts"]),
]);

export default eslintConfig;
