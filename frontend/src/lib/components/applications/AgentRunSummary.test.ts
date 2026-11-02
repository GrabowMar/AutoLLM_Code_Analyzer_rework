// Agent Run tab, Reproducibility list: long image refs and routing JSON must be
// able to shrink (min-w-0 on the flex row and the value) so they ellipsize at
// phone width instead of being clipped; the full value stays in the tooltip.
import { describe, expect, it } from "vitest";
import { render } from "svelte/server";
import type { GenerationJob } from "$lib/api/client";
import AgentRunSummary from "./AgentRunSummary.svelte";

const images = {
  base: "llmlab/agent-base:8c639a2ec7f6",
  gateway: "llmlab/llm-gateway:d6341983d9a9",
  harness: "llmlab/agent-opencode:06772cb897ce",
};
const provider = {
  quantizations: ["fp8", "fp16", "bf16", "fp32", "unknown"],
  allow_fallbacks: true,
  require_parameters: true,
};

function job(): GenerationJob {
  return {
    id: "f6569f45-be1b-4a04-a88c-72ba7d896a66",
    mode: "agent",
    status: "completed",
    prompt_hash: "ea88848e2aefb8ef",
    metrics: {},
    result_data: {
      agent: {
        harness: "opencode",
        harness_version: "1.0",
        termination: "completed",
        network_policy: "gateway-only",
      },
    },
    resolved_bundle: {
      run_fingerprint: "17fafebbde9bd77b",
      images,
      llm: { provider },
      harness: { system_prompt_source: "harness default (opencode)" },
      brief: { version: 1, sha256: "a".repeat(64) },
    },
  } as unknown as GenerationJob;
}

describe("AgentRunSummary reproducibility rows", () => {
  // Without Svelte's hydration markers (<!--[-->, <!---->).
  // Repeat until stable so nested/overlapping markers cannot leave a partial "<!--" behind.
  let body = render(AgentRunSummary, { props: { job: job() } }).body;
  for (let prev = ""; prev !== body; ) {
    prev = body;
    body = body.replace(/<!--[\s\S]*?-->/g, "");
  }
  const imagesText = `${images.harness} on ${images.base}`;
  const routingText = JSON.stringify(provider);
  const re = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

  it.each([
    ["Harness prompt", "harness default (opencode)", "min-w-0 truncate"],
    ["Images", imagesText, "min-w-0 truncate font-mono"],
    ["Routing", routingText, "min-w-0 truncate font-mono"],
  ])(
    "lets the %s value shrink and ellipsize, full value in the title",
    (label, text, ddClass) => {
      const row = new RegExp(
        `<div class="flex min-w-0 justify-between gap-2 sm:col-span-2"><dt class="shrink-0 text-muted-foreground">${label}</dt>` +
          `<dd class="${ddClass}" title="${re(text.replace(/"/g, "&quot;"))}">${re(text)}</dd></div>`,
      );
      expect(body).toMatch(row);
    },
  );
});
