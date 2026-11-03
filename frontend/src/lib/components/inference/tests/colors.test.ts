import { describe, expect, it } from "vitest";
import {
  cellStateLabels,
  cellStateTones,
  cellTone,
  liveStatuses,
  outcomeLabels,
  outcomeTones,
  statusTone,
  type CellState,
  type RunOutcome,
} from "$lib/constants/colors";

describe("pipeline tones", () => {
  it("maps the new statuses", () => {
    expect(statusTone("eval_queued")).toBe("neutral");
    expect(statusTone("evaluating")).toBe("info");
    expect(statusTone("backoff")).toBe("warning");
    expect(statusTone("warming_model")).toBe("info");
    expect(statusTone("online")).toBe("success");
    expect(statusTone("offline")).toBe("danger");
    expect(statusTone("paused")).toBe("warning");
    expect(liveStatuses.has("evaluating")).toBe(true);
  });
  it("keeps the existing ones", () => {
    expect(statusTone("completed")).toBe("success");
    expect(statusTone("failed")).toBe("danger");
    expect(statusTone(undefined)).toBe("neutral");
  });
  it("tones cells by state, and done cells by their run outcome", () => {
    for (const state of Object.keys(cellStateTones) as CellState[]) {
      expect(cellStateTones[state]).toBeDefined();
      expect(cellStateLabels[state]).toBeTruthy();
    }
    for (const outcome of Object.keys(outcomeTones) as RunOutcome[]) {
      expect(outcomeLabels[outcome]).toBeTruthy();
    }
    expect(cellTone("done", "tested")).toBe("success");
    expect(cellTone("done", "build_fail")).toBe("danger");
    // Attrition is missing data, never a failure.
    expect(cellTone("done", "infra_error")).toBe("warning");
    expect(cellTone("infra_pending")).toBe("warning");
    // A done cell without a known outcome is never shown as success.
    expect(cellTone("done")).toBe("neutral");
    expect(cellTone("running")).toBe("info");
    expect(cellTone("?")).toBe("neutral");
    expect(cellTone(null)).toBe("neutral");
  });
});
