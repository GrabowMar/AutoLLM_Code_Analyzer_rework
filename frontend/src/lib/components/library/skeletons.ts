/**
 * Starting content for a new library entity of each editable kind (the "blank" choice on
 * `/library/[kind]/new`). Budget presets start from a tier preset (`GET /budget/knobs/`.tiers) instead.
 * Refs that must point at a real entity (fragments, prompt-set roles) start empty, so the server's
 * validation names what is missing.
 */
import type { LibraryContentMap, LibraryKind } from "$lib/api/v3/types";
import { emptyTaskContent } from "./task";

export function blankContent(
  kind: LibraryKind,
): LibraryContentMap[LibraryKind] | null {
  switch (kind) {
    case "task":
      return emptyTaskContent();
    case "stack":
      return {
        choice_mode: "fixed",
        backend: {
          language: "python",
          framework: "flask",
          runtime: "python3.13",
        },
        frontend: { framework: "react", language: "ts", build_tool: "vite" },
        database: "sqlite",
        run_contract: {
          port: 8000,
          api_prefix: "/api",
          health_path: "/api/health",
          static_serving: "backend",
          data_dir: "./data",
          start: "dockerfile",
        },
        brief_fragment: "",
        adherence: {
          backend_markers: [],
          frontend_markers: [],
          db_markers: [],
        },
        resources: { min_memory: "2g", min_cpus: 1 },
      };
    case "style":
      return {
        system: "plain-css",
        library: null,
        tone: null,
        tone_text: null,
        accessibility: "unspecified",
        fragment: "",
        adherence: {
          required_markers: [],
          forbidden_markers: [],
          class_share_min: null,
        },
        compatible_frontends: ["react", "vue", "svelte"],
      };
    case "guidance":
      return {
        security_hint: "none",
        cwe_source: "task_surfaces",
        cwes: [],
        library_prescriptions: [],
        fragment: null,
      };
    case "prompt":
      return {
        role: "brief",
        engine: "jinja2-sandbox@1",
        template: "",
        variables: [],
        modes: ["app"],
        tags: [],
      };
    case "prompt_set":
      return {
        mode: "app",
        roles: {},
        system_prompt_mode: "harness_native",
        fragments: {},
      };
    case "harness_preset":
      return {
        harness: "opencode",
        harness_version: "",
        options: {},
        controlled: false,
        command_timeout_s: null,
        tool_policy: { deny: [], allow: null },
        max_turns: null,
        subagents: false,
        pin_system_prompt: false,
      };
    case "sampling":
      return {
        policy: "explicit",
        params: {
          temperature: 0.7,
          top_p: null,
          top_k: null,
          min_p: null,
          frequency_penalty: null,
          presence_penalty: null,
        },
        forward_seed: true,
      };
    case "detector":
      return {
        engine: "regex",
        languages: [],
        rule: "",
        tool: { name: "regex", version: "1" },
        feature_key: "",
        polarity: "insecure",
        tool_severity_field: null,
        cwe: [],
        description: "",
        scopes: ["span", "file"],
        taint: false,
      };
    default:
      return null;
  }
}
