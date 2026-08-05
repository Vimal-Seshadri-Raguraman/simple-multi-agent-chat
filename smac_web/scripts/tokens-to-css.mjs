#!/usr/bin/env node
// Regenerates src/styles/tokens.css from design/tokens.json -- the single
// source of truth for the constitution's design tokens (design system spec
// §2). Every surface consumes that file; this script is the web build's
// compiler for it. Never hand-edit the generated CSS -- edit
// design/tokens.json and rerun `npm run tokens` (or `node
// scripts/tokens-to-css.mjs`) instead.
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const TOKENS_PATH = path.resolve(
  __dirname,
  "..",
  "..",
  "design",
  "tokens.json"
);
const OUT_PATH = path.resolve(__dirname, "..", "src", "styles", "tokens.css");

const tokens = JSON.parse(readFileSync(TOKENS_PATH, "utf8"));

function colorLines(theme) {
  return Object.entries(tokens.color)
    .map(([name, values]) => `  --color-${name}: ${values[theme]};`)
    .join("\n");
}

const spaceLines = tokens.space
  .map((value, index) => `  --space-${index}: ${value}px;`)
  .join("\n");

const radiusLines = Object.entries(tokens.radius)
  .map(([name, value]) => `  --radius-${name}: ${value}px;`)
  .join("\n");

const scaleLines = Object.entries(tokens.type.scale)
  .map(([name, value]) => `  --font-size-${name}: ${value}px;`)
  .join("\n");

const css = `/* GENERATED FILE -- do not edit by hand.
 * Source of truth: design/tokens.json (design system constitution §2).
 * Regenerate with \`npm run tokens\` after changing that file.
 */

:root {
${colorLines("light")}
${spaceLines}
${radiusLines}
  --font-ui: ${tokens.type.ui};
  --font-mono: ${tokens.type.mono};
${scaleLines}
}

[data-theme="dark"] {
${colorLines("dark")}
}
`;

writeFileSync(OUT_PATH, css);
console.log(`Wrote ${path.relative(process.cwd(), OUT_PATH)}`);
