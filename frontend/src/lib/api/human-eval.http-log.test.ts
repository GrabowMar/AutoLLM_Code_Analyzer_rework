import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();
vi.mock("./core", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
}));

const { listHttpLog } = await import("./human-eval");

describe("listHttpLog", () => {
  beforeEach(() => {
    apiFetch.mockReset();
    apiFetch.mockResolvedValue(
      new Response(
        JSON.stringify({
          items: [],
          total: 0,
          page: 2,
          per_page: 100,
          pages: 2,
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );
  });

  it("sends the assignment filter the backend applies (the sheet no longer filters a participant's newest 100)", async () => {
    await listHttpLog("task-1", {
      assignment_id: "asg-1",
      page: 2,
      per_page: 100,
    });
    expect(apiFetch).toHaveBeenCalledTimes(1);
    const url = new URL(String(apiFetch.mock.calls[0][0]), "http://x");
    expect(url.pathname).toBe("/human-eval/tasks/task-1/http-log/");
    expect(url.searchParams.get("assignment_id")).toBe("asg-1");
    expect(url.searchParams.get("page")).toBe("2");
    expect(url.searchParams.get("per_page")).toBe("100");
    expect(url.searchParams.has("participant_id")).toBe(false);
  });
});
