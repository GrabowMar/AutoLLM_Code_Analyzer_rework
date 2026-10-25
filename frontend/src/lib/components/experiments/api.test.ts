import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createExperiment,
  createdExperiment,
  type ExperimentDetail,
  type LaunchResult,
} from "$lib/api/experiments";

const detail = { id: "e1", name: "Exp", status: "draft" } as ExperimentDetail;

describe("createdExperiment", () => {
  it("passes a plain detail through", () => {
    expect(createdExperiment(detail)).toBe(detail);
  });

  it("unwraps a launch result (201) into the detail plus its batch", () => {
    const launched = {
      experiment: { ...detail, status: "running" },
      batch: {
        id: "b1",
        kind: "launch",
        created_at: "",
        total_jobs: 4,
      },
      jobs_created: 4,
      policy_effective_by_endpoint: {},
      warnings: [],
    } as unknown as LaunchResult;
    const out = createdExperiment(launched);
    expect(out.id).toBe("e1");
    expect(out.status).toBe("running");
    expect(out.batch?.id).toBe("b1");
  });
});

describe("createExperiment", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("returns the experiment id after create-and-launch", async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            experiment: { ...detail, status: "running" },
            batch: null,
            jobs_created: 2,
            policy_effective_by_endpoint: {},
            warnings: [],
          }),
          { status: 201, headers: { "content-type": "application/json" } },
        ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const out = await createExperiment(
      {} as Parameters<typeof createExperiment>[0],
      { launch: true },
    );
    expect(out.id).toBe("e1");
    expect(String(fetchMock.mock.calls[0]).includes("?launch=true")).toBe(true);
  });
});
