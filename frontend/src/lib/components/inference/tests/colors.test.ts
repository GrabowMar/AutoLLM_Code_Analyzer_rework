import { describe, expect, it } from "vitest";
import {
  cellCodeLabels,
  cellCodeTones,
  cellTone,
  liveStatuses,
  statusTone,
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
  it("covers every cell code", () => {
    const codes = [
      "p",
      "b",
      "w",
      "r",
      "q",
      "e",
      "c",
      "f",
      "u",
      "x",
      "k",
    ] as const;
    for (const code of codes) {
      expect(cellCodeTones[code]).toBeDefined();
      expect(cellCodeLabels[code]).toBeTruthy();
    }
    expect(cellTone("c")).toBe("success");
    expect(cellTone("f")).toBe("danger");
    expect(cellTone("x")).toBe("warning");
    expect(cellTone("?")).toBe("neutral");
    expect(cellTone(null)).toBe("neutral");
  });
});
