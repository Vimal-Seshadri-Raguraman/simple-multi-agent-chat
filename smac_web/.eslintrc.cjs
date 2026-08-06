module.exports = {
  root: true,
  env: { browser: true, es2021: true, node: true },
  extends: [
    "eslint:recommended",
    "plugin:@typescript-eslint/recommended",
    "plugin:react/recommended",
    "plugin:react-hooks/recommended",
  ],
  parser: "@typescript-eslint/parser",
  parserOptions: {
    ecmaVersion: "latest",
    sourceType: "module",
    ecmaFeatures: { jsx: true },
  },
  plugins: ["react", "react-hooks", "@typescript-eslint"],
  settings: { react: { version: "18.3" } },
  ignorePatterns: ["dist", "node_modules"],
  rules: {
    // SMAC security stage (constitution §7.5; web spec §4): message/handle/
    // name content renders as text nodes only, never raw HTML. This is the
    // load-bearing rule for that guarantee -- do not weaken or suppress it
    // per-file; render untrusted strings as text and derive markup from
    // known-safe data instead.
    "react/no-danger": "error",
    // React 17+ JSX runtime (configured via tsconfig's "jsx": "react-jsx")
    // means React doesn't need to be in scope for JSX to work.
    "react/react-in-jsx-scope": "off",
    "@typescript-eslint/no-unused-vars": [
      "error",
      { argsIgnorePattern: "^_" },
    ],
  },
};
