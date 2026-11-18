import { describe, expect, it, vi } from "vitest";
import {
  runNotebook,
  withTimeout,
  type LiteApp,
  type NotebookPanel,
} from "./jupyter";

function fixture(
  success: unknown = true,
  outputs: Array<{ output_type: string; ename?: string; evalue?: string }> = [],
) {
  const save = vi.fn(async () => {});
  const panel: NotebookPanel = {
    id: "report-panel",
    context: {
      ready: Promise.resolve(),
      save,
      model: { toJSON: () => ({ cells: [{ outputs }] }), fromJSON: vi.fn() },
    },
  };
  const execute = vi.fn(async (id: string) =>
    id === "docmanager:open" ? panel : success,
  );
  const app: LiteApp = {
    started: Promise.resolve(),
    restored: Promise.resolve(),
    serviceManager: { contents: { get: vi.fn(), save: vi.fn() } },
    commands: { execute, hasCommand: () => true },
    shell: { activateById: vi.fn() },
  };
  return { app, execute, save, panel };
}

describe("embedded notebook execution", () => {
  it("targets the report panel and waits for execution before saving outputs", async () => {
    const { app, execute, save } = fixture();
    let finish!: (result: boolean) => void;
    execute.mockImplementation(async (id: string) =>
      id === "notebook:run-all-cells"
        ? new Promise<boolean>((resolve) => {
            finish = resolve;
          })
        : {
            id: "report-panel",
            context: {
              ready: Promise.resolve(),
              save,
              model: { toJSON: () => ({ cells: [] }) },
            },
          },
    );
    const running = runNotebook(app, "reports/report/report.ipynb");
    await vi.waitFor(() => expect(finish).toBeDefined());
    expect(save).not.toHaveBeenCalled();
    expect(execute).toHaveBeenCalledWith("notebook:run-all-cells", {
      widgetId: "report-panel",
    });
    finish(true);
    await running;
    expect(save).toHaveBeenCalledOnce();
  });

  it("reports a Python cell error and still saves its diagnostic output", async () => {
    const { app, save } = fixture(false, [
      { output_type: "error", ename: "ValueError", evalue: "Missing data" },
    ]);
    await expect(runNotebook(app, "report.ipynb")).rejects.toThrow(
      "ValueError: Missing data",
    );
    expect(save).toHaveBeenCalledOnce();
  });

  it("starts the Python session before running cells instead of waiting for a kernel picker", async () => {
    const { app, panel, execute } = fixture();
    const initialize = vi.fn(async () => true);
    const changeKernel = vi.fn(async () => {});
    panel.context.sessionContext = {
      ready: Promise.resolve(),
      session: null,
      initialize,
      changeKernel,
    };
    await runNotebook(app, "report.ipynb");
    expect(execute).toHaveBeenCalledWith("docmanager:open", {
      path: "report.ipynb",
      factory: "Notebook",
      kernelPreference: {
        name: "python",
        language: "python",
        autoStartDefault: true,
      },
    });
    expect(initialize).toHaveBeenCalledOnce();
    expect(changeKernel).toHaveBeenCalledWith({ name: "python" });
  });

  it("does not call a stopped or unavailable execution successful", async () => {
    await expect(
      runNotebook(fixture(false).app, "report.ipynb"),
    ).rejects.toThrow("Execution stopped");
    await expect(
      runNotebook(fixture(null).app, "report.ipynb"),
    ).rejects.toThrow("Execution stopped");
    const { app } = fixture();
    app.commands.hasCommand = () => false;
    await expect(runNotebook(app, "report.ipynb")).rejects.toThrow(
      "unavailable",
    );
  });

  it("times out a stalled startup promise", async () => {
    await expect(
      withTimeout(new Promise(() => {}), 5, "startup timeout"),
    ).rejects.toThrow("startup timeout");
    await expect(
      withTimeout(Promise.resolve("ready"), 5, "startup timeout"),
    ).resolves.toBe("ready");
  });
});
