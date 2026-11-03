// WP-17: the shared API modules re-pointed from v2 jobs/experiments to v3 runs/studies.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getQueue } from "./execution";
import {
  functionalSourceLabel,
  functionalStatusLabel,
  listCandidateRuns,
  studyOptionDescription,
  addTaskApps,
} from "./human-eval";
import {
  getRunCodeProfile,
  getRunQualityMetrics,
  getRunTimeline,
} from "./job-insights";
import { REPORT_TYPE_LABELS, summaryItems } from "./reports";
import { callsCsvUrl, getModelCallTelemetry } from "./telemetry";
import { getModelBehaviour } from "./trajectory";
import {
  RUN_EVENT_TYPES,
  REWORK_EVENT_TYPES,
  STUDY_EVENT_TYPES,
  runChannel,
  studyChannel,
} from "./types/events";
import {
  downloadStudyVariabilityCsv,
  getModelVariability,
  getRunReplicates,
  getStudyVariability,
  variabilityQuery,
} from "./variability";
import { SSE_EVENT_NAMES } from "./sse";
import {
  cellStateLabels,
  cellStateTones,
  cellTone,
  outcomeLabels,
  outcomeTones,
  statusTone,
} from "$lib/constants/colors";

let urls: string[];
let bodies: (string | null)[];

beforeEach(() => {
  urls = [];
  bodies = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      urls.push(url);
      bodies.push(typeof init.body === "string" ? init.body : null);
      return new Response("{}", {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const RUN = "7d0b9c1e-0000-4000-8000-000000000001";
const STUDY = "5a6b7c8d-0000-4000-8000-000000000002";

describe("run and study URLs", () => {
  it("reads run insights under /generation/runs/", async () => {
    await getRunCodeProfile(RUN);
    await getRunTimeline(RUN);
    await getRunQualityMetrics(RUN);
    await getRunReplicates(RUN);
    expect(urls).toEqual([
      `/api/generation/runs/${RUN}/code-profile/`,
      `/api/generation/runs/${RUN}/timeline/`,
      `/api/generation/runs/${RUN}/quality-metrics/`,
      `/api/generation/runs/${RUN}/replicates/`,
    ]);
  });

  it("reads study variability with fingerprint and the ITT sensitivity only", async () => {
    await getStudyVariability(STUDY);
    await getStudyVariability(STUDY, {
      fingerprint: "all",
      sensitivity: "exclude_truncated",
    });
    await downloadStudyVariabilityCsv(STUDY, { fingerprint: "majority" });
    expect(urls).toEqual([
      `/api/generation/studies/${STUDY}/variability/`,
      `/api/generation/studies/${STUDY}/variability/?fingerprint=all&sensitivity=exclude_truncated`,
      `/api/generation/studies/${STUDY}/variability/?fingerprint=majority&format=csv`,
    ]);
    // A null sensitivity is intention-to-treat: nothing is sent.
    expect(variabilityQuery({ sensitivity: null })).toBe("");
  });

  it("filters model telemetry by run kind and the calls CSV by study and run", async () => {
    await getModelCallTelemetry(4, { kind: "app_agentic" });
    await getModelBehaviour(4);
    await getModelVariability(4);
    expect(urls).toEqual([
      "/api/generation/models/4/call-telemetry/?kind=app_agentic",
      "/api/generation/models/4/behaviour/",
      "/api/generation/models/4/variability/",
    ]);
    expect(callsCsvUrl({ study: STUDY, run: RUN, model: 4 })).toBe(
      `/api/generation/calls.csv?study=${STUDY}&run=${RUN}&model=4`,
    );
  });

  it("narrows the executor queue to a study", async () => {
    await getQueue({ study_id: STUDY, endpoint: "gpu1", limit: 50 });
    expect(urls[0]).toBe(
      `/api/execution/queue/?study_id=${STUDY}&endpoint=gpu1&limit=50`,
    );
  });

  it("lists human-eval candidate runs and adds a study or runs to a pool", async () => {
    await listCandidateRuns({
      study_id: STUDY,
      current_only: true,
      page: 2,
      per_page: 25,
    });
    expect(urls[0]).toBe(
      `/api/human-eval/candidate-runs/?study_id=${STUDY}&current_only=true&page=2&per_page=25`,
    );
    await addTaskApps("t1", { study_id: STUDY, current_only: true });
    await addTaskApps("t1", { run_ids: [RUN] });
    expect(JSON.parse(bodies[1] ?? "{}")).toEqual({
      study_id: STUDY,
      current_only: true,
    });
    expect(JSON.parse(bodies[2] ?? "{}")).toEqual({ run_ids: [RUN] });
  });
});

describe("validity of displayed values", () => {
  it("never shows a missing evaluation as a pass or a zero", () => {
    expect(functionalStatusLabel(null)).toBe("Not measured");
    expect(functionalStatusLabel("not_measured")).toBe("Not measured");
    expect(functionalStatusLabel("tested")).toBe("Tested");
    expect(functionalStatusLabel("build_fail")).toBe("Build failed");
    expect(functionalStatusLabel("some_new_status")).toBe("some new status");
    expect(functionalSourceLabel("eval_suite")).toBe("EvalSuite");
    expect(functionalSourceLabel(null)).toBe("");
    expect(statusTone("not_measured")).toBe("warning");
    expect(statusTone("tested")).toBe("success");
  });

  it("colours a done cell by its run's outcome and treats attrition as missing data", () => {
    expect(cellTone("done", "tested")).toBe("success");
    expect(cellTone("done", "build_fail")).toBe("danger");
    expect(cellTone("done", "infra_error")).toBe("warning");
    expect(cellTone("done", "")).toBe("neutral");
    expect(cellTone("done", null)).toBe("neutral");
    expect(cellTone("infra_pending")).toBe("warning");
    expect(cellTone("running")).toBe("info");
    expect(cellTone("excluded")).toBe("neutral");
    expect(cellTone("bogus")).toBe("neutral");
    expect(cellTone(null)).toBe("neutral");
    for (const state of Object.keys(cellStateTones)) {
      expect(
        cellStateLabels[state as keyof typeof cellStateLabels],
      ).toBeTruthy();
    }
    for (const outcome of Object.keys(outcomeTones)) {
      expect(outcomeLabels[outcome as keyof typeof outcomeLabels]).toBeTruthy();
      expect(statusTone(outcome)).toBe(
        outcomeTones[outcome as keyof typeof outcomeTones],
      );
    }
  });

  it("describes a study for pickers by its main-stage cells", () => {
    expect(
      studyOptionDescription({
        status: "running",
        mode: "app",
        kind: "confirmatory",
        cells_done: 3,
        cells_total: 40,
      }),
    ).toBe("running · app mode · confirmatory · 3/40 cells");
    expect(
      studyOptionDescription({
        status: "planned",
        mode: "slot",
        kind: "exploratory",
        cells_done: 0,
        cells_total: 0,
      }),
    ).toBe("planned · slot mode · exploratory · no cells yet");
  });
});

describe("reports", () => {
  it("labels the re-purposed report types and links a study summary", () => {
    expect(REPORT_TYPE_LABELS.template_comparison).toBe("Task comparison");
    expect(REPORT_TYPE_LABELS.experiment_report).toBe("Study conditions");
    expect(summaryItems({ study_id: STUDY })).toEqual([
      { label: "Study", value: STUDY.slice(0, 8), href: `/studies/${STUDY}` },
    ]);
  });
});

describe("SSE events", () => {
  it("registers every run and study event name and no v2 job event", () => {
    for (const name of [
      ...RUN_EVENT_TYPES,
      ...STUDY_EVENT_TYPES,
      "endpoint",
      "session",
      "capacity",
    ]) {
      expect(REWORK_EVENT_TYPES).toContain(name);
      expect(SSE_EVENT_NAMES).toContain(name);
    }
    expect(REWORK_EVENT_TYPES).not.toContain("job");
    expect(new Set(REWORK_EVENT_TYPES).size).toBe(REWORK_EVENT_TYPES.length);
    expect(runChannel(RUN)).toBe(`run:${RUN}`);
    expect(studyChannel(STUDY)).toBe(`study:${STUDY}`);
  });
});
