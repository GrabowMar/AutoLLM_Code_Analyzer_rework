// Server-side renders of the Behaviour card: unmeasured values render as "—"
// (never 0), and policy denials are shown apart from failed actions.
import { describe, expect, it } from "vitest";
import { render } from "svelte/server";
import type { TrajectoryRecord } from "$lib/api/trajectory";
import BehaviourCard from "./BehaviourCard.svelte";

function record(overrides: Partial<TrajectoryRecord> = {}): TrajectoryRecord {
  return {
    schema_version: 1,
    parser_version: "1.1",
    comparable: true,
    tool_calls_gateway: 10,
    actions_total: 10,
    failed_actions: 2,
    killed_actions: 1,
    policy_denials: 3,
    edit_no_match: 0,
    files_written: 4,
    max_writes_per_file: 2,
    writes_after_first_build: null,
    cache_hit_ratio: null,
    reasoning_share: null,
    failure_kinds: { killed: 1, nonzero_exit: 1, policy_denial: 3 },
    verification: {
      built: false,
      ran_server: true,
      probed_http: true,
      ran_tests: false,
      syntax_checked: false,
      n_builds: 0,
      builds_ok: 0,
      builds_failed: 0,
      last_build_ok: null,
      first_build_action: null,
      first_probe_action: 3,
    },
    time: {
      llm_s: null,
      between_calls_s: null,
      llm_share: null,
      tool_exec_s: null,
      first_call_at: null,
      last_call_at: null,
      head_s: null,
      tail_s: null,
    },
    context: {
      prompt_tokens_first: null,
      prompt_tokens_last: null,
      prompt_tokens_max: null,
      series: [],
    },
    ...overrides,
  };
}

describe("BehaviourCard", () => {
  it("renders unmeasured values as a dash, not 0", () => {
    const { body } = render(BehaviourCard, { props: { record: record() } });
    expect(body).toContain("Cache hits");
    // cache ratio, reasoning share, LLM share and the context first/peak are unmeasured
    expect(body).toMatch(/kpi-value">—</);
    expect(body).not.toMatch(/kpi-value">0%</);
    expect(body).toContain("No per-call prompt tokens recorded.");
  });

  it("shows policy denials apart from failed actions", () => {
    const { body } = render(BehaviourCard, { props: { record: record() } });
    expect(body).toContain("plus 3 denied by harness policy");
    expect(body).toContain("denied by harness policy · 3");
  });

  it("hides action metrics for a harness without tool calls", () => {
    const { body } = render(BehaviourCard, {
      props: {
        record: record({
          comparable: false,
          comparable_reason: "single-turn edit format",
          actions_total: null,
          failed_actions: null,
          killed_actions: null,
          policy_denials: null,
          failure_kinds: {},
        }),
      },
    });
    expect(body).toContain(
      "Action metrics do not apply to this run: single-turn edit format",
    );
    expect(body).not.toContain("Failed actions");
  });
});
