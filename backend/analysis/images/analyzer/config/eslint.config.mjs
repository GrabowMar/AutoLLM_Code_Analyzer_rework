// Baked ESLint flat config of the llmlab analyzer (ESLint 10, typescript-eslint 8, eslint-plugin-vue 10).
//
// The adapter runs `eslint --no-config-lookup -c <this file> --no-inline-config` from /src, so an
// analysed app can neither replace this config nor switch rules off with comments.
//
// Research rule: rule semantics do not depend on the file's language. Identical code yields the same
// findings in .js, .jsx, .ts, .tsx and in a Vue SFC (<script> or <script lang="ts">); only constructs
// that exist in one language alone (TypeScript types, Vue templates) have rules of their own.
//   * every script file   @eslint/js recommended (the language-neutral correctness baseline),
//                         eslint-plugin-security, eslint-plugin-no-unsanitized, core eval rules,
//                         react-hooks/rules-of-hooks
//   * .vue                + eslint-plugin-vue essential (vue-eslint-parser)
//   * TypeScript          .ts/.tsx/.mts/.cts and Vue SFCs whose <script> is lang="ts"/"tsx": the
//                         typescript-eslint parser (no type information: dependencies are not installed)
//                         and only those typescript-eslint recommended rules that check TypeScript-only
//                         syntax. Core rules that misread TypeScript syntax are replaced or switched off:
//                         no-unused-vars -> @typescript-eslint/no-unused-vars (types, overloads, enums);
//                         no-undef (type references such as NodeJS.Timeout), no-redeclare and
//                         no-dupe-class-members (overloads, declaration merging) are off. The other core
//                         rules typescript-eslint's "eslint-recommended" switches off assume the TypeScript
//                         compiler runs, which it does not here, so they stay on. typescript-eslint rules
//                         that also fire on plain JavaScript (no-var, prefer-const, prefer-rest-params,
//                         prefer-spread, no-unused-expressions, no-require-imports, no-this-alias,
//                         no-array-constructor) are off: the JavaScript baseline does not have them.
//   * JSX                 the automatic runtime (Vite): no JSX pragma, so an `import React` that only JSX
//                         uses is unused in .jsx and .tsx alike (typescript-eslint's default pragma
//                         "React" would mark it used in .tsx only).
// ESLint 10 tracks JSX references in scope analysis, so components used only as <Component /> are not
// reported by no-unused-vars (verified on generated React apps; eslint-plugin-react is not needed).
import { readFileSync } from "node:fs";
import js from "@eslint/js";
import globals from "globals";
import noUnsanitized from "eslint-plugin-no-unsanitized";
import reactHooks from "eslint-plugin-react-hooks";
import security from "eslint-plugin-security";
import vue from "eslint-plugin-vue";
import tseslint from "typescript-eslint";

const JS_FILES = ["**/*.js", "**/*.jsx", "**/*.mjs", "**/*.cjs"];
const TS_FILES = ["**/*.ts", "**/*.tsx", "**/*.mts", "**/*.cts"];
const VUE_FILES = ["**/*.vue"];

// A Vue single-file component whose <script> block is TypeScript (vue-eslint-parser hands it to the
// typescript-eslint parser). Flat config matches files by path only, so this matcher reads the file.
const TS_SCRIPT_RE = /<script\b[^>]*?\slang\s*=\s*["']?tsx?["'\s>]/i;
const sfcIsTypeScript = new Map();
function isTypeScriptSfc(filePath) {
  if (!filePath.endsWith(".vue")) return false;
  if (!sfcIsTypeScript.has(filePath)) {
    let ts = false;
    try {
      ts = TS_SCRIPT_RE.test(readFileSync(filePath, "utf8"));
    } catch {
      ts = false;
    }
    sfcIsTypeScript.set(filePath, ts);
  }
  return sfcIsTypeScript.get(filePath);
}

// Generated apps mix browser code (frontend) and Node code (build config, servers).
const GLOBALS = { ...globals.browser, ...globals.node };

const SECURITY_RULES = {
  "no-eval": "error",
  "no-implied-eval": "error",
  "no-new-func": "error",
  "no-script-url": "error",
  "security/detect-bidi-characters": "error",
  "security/detect-buffer-noassert": "error",
  "security/detect-child-process": "error",
  "security/detect-disable-mustache-escape": "error",
  "security/detect-eval-with-expression": "error",
  "security/detect-new-buffer": "error",
  "security/detect-no-csrf-before-method-override": "error",
  "security/detect-non-literal-regexp": "error",
  "security/detect-non-literal-require": "error",
  "security/detect-possible-timing-attacks": "error",
  "security/detect-pseudoRandomBytes": "error",
  "security/detect-unsafe-regex": "error",
  // Flag nearly every obj[key] / fs call in idiomatic code; profiles can opt in (adapter --rule).
  "security/detect-object-injection": "off",
  "security/detect-non-literal-fs-filename": "off",
  "no-unsanitized/method": "error",
  "no-unsanitized/property": "error",
  "react-hooks/rules-of-hooks": "error",
};

// typescript-eslint recommended, rules only (eslint-recommended overrides + recommended set).
const TS_RECOMMENDED = Object.assign({}, ...tseslint.configs.recommended.map((config) => config.rules ?? {}));
// Core rules typescript-eslint switches off that work on TypeScript syntax without type information.
const KEEP_CORE = [
  "constructor-super",
  "getter-return",
  "no-class-assign",
  "no-const-assign",
  "no-dupe-args",
  "no-dupe-keys",
  "no-func-assign",
  "no-import-assign",
  "no-new-native-nonconstructor",
  "no-obj-calls",
  "no-setter-return",
  "no-this-before-super",
  "no-unreachable",
  "no-unsafe-negation",
  "no-with",
];
// Rules typescript-eslint adds that also fire on plain JavaScript (not in the JavaScript baseline).
const NOT_IN_JS_BASELINE = [
  "no-var",
  "prefer-const",
  "prefer-rest-params",
  "prefer-spread",
  "@typescript-eslint/no-unused-expressions",
  "@typescript-eslint/no-require-imports",
  "@typescript-eslint/no-this-alias",
  "@typescript-eslint/no-array-constructor",
];
const TS_RULES = { ...TS_RECOMMENDED };
for (const rule of KEEP_CORE) delete TS_RULES[rule];
for (const rule of NOT_IN_JS_BASELINE) TS_RULES[rule] = "off";
// Switched off because TypeScript syntax makes them misfire (see the header).
Object.assign(TS_RULES, { "no-undef": "off", "no-redeclare": "off", "no-dupe-class-members": "off" });

const vueEssential = vue.configs["flat/essential"].map((config) => ({ ...config, files: VUE_FILES }));

export default [
  {
    name: "llmlab/linter-options",
    linterOptions: { noInlineConfig: true, reportUnusedDisableDirectives: "off" },
  },
  {
    // Plugins are registered globally so the adapter's --rule overrides resolve for every file type.
    name: "llmlab/plugins",
    plugins: {
      security,
      "no-unsanitized": noUnsanitized,
      "react-hooks": reactHooks,
      "@typescript-eslint": tseslint.plugin,
    },
  },
  {
    name: "llmlab/core",
    files: [...JS_FILES, ...TS_FILES, ...VUE_FILES],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: GLOBALS,
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    rules: { ...js.configs.recommended.rules },
  },
  {
    name: "llmlab/commonjs",
    files: ["**/*.cjs", "**/*.cts"],
    languageOptions: { sourceType: "commonjs" },
  },
  {
    name: "llmlab/typescript-parser",
    files: TS_FILES,
    languageOptions: {
      parser: tseslint.parser,
      parserOptions: { ecmaFeatures: { jsx: true }, jsxPragma: null },
    },
  },
  ...vueEssential,
  {
    name: "llmlab/vue",
    files: VUE_FILES,
    languageOptions: {
      parserOptions: {
        parser: { js: "espree", jsx: "espree", ts: tseslint.parser, tsx: tseslint.parser },
        ecmaFeatures: { jsx: true },
        extraFileExtensions: [".vue"],
        jsxPragma: null,
      },
    },
    rules: {
      // Template comments (<!-- eslint-disable -->) are honoured by this rule even with
      // --no-inline-config; switching it off keeps the app from disabling its own scanner.
      "vue/comment-directive": "off",
      // Component naming convention, not a defect.
      "vue/multi-word-component-names": "off",
      "vue/no-v-html": "error",
    },
  },
  {
    name: "llmlab/typescript-rules",
    files: [...TS_FILES, isTypeScriptSfc],
    rules: TS_RULES,
  },
  {
    name: "llmlab/security",
    files: [...JS_FILES, ...TS_FILES, ...VUE_FILES],
    rules: SECURITY_RULES,
  },
];
