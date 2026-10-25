import { describe, expect, it } from "vitest";
import type { GenerationJobList } from "$lib/api/generation";
import {
  jobDisplayStatus,
  trialOutcome,
} from "$lib/components/generator/batches/batch-utils";

function job(
  status: GenerationJobList["status"],
  phase: GenerationJobList["phase"],
): GenerationJobList {
  return { status, phase } as GenerationJobList;
}

describe("jobDisplayStatus", () => {
  it("shows a generated run waiting for evaluation as queued", () => {
    expect(jobDisplayStatus(job("running", "eval_queued"))).toBe("queued");
    expect(jobDisplayStatus(job("running", "queued"))).toBe("queued");
  });

  it("keeps running, evaluating and finished statuses", () => {
    expect(jobDisplayStatus(job("running", "running"))).toBe("running");
    expect(jobDisplayStatus(job("running", "evaluating"))).toBe("evaluating");
    expect(jobDisplayStatus(job("completed", "eval_queued"))).toBe("completed");
  });

  it("counts an eval_queued trial as queued, not running", () => {
    expect(trialOutcome(job("running", "eval_queued"), true)).toBe("queued");
  });
});
