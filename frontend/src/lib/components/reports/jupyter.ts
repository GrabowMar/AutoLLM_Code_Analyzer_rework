/** The public JupyterLab interfaces used by the embedded report notebook. */
export interface NotebookPanel {
  id: string;
  context: {
    ready: Promise<void>;
    save: () => Promise<void>;
    sessionContext?: {
      ready: Promise<void>;
      session: { kernel?: { name: string } | null } | null;
      initialize: () => Promise<boolean>;
      changeKernel: (options: { name: string }) => Promise<unknown>;
    };
    model: {
      toJSON: () => {
        cells?: Array<{
          outputs?: Array<{
            output_type?: string;
            ename?: string;
            evalue?: string;
          }>;
        }>;
      };
      fromJSON: (value: unknown) => void;
    };
  };
}

export interface LiteApp {
  started: Promise<void>;
  restored: Promise<void>;
  serviceManager: {
    contents: {
      get: (
        path: string,
        opts?: { content?: boolean; format?: string },
      ) => Promise<{ content?: unknown; format?: string }>;
      save: (path: string, model: Record<string, unknown>) => Promise<unknown>;
    };
  };
  commands: {
    execute: (id: string, args?: Record<string, unknown>) => Promise<unknown>;
    hasCommand: (id: string) => boolean;
  };
  shell: { collapseLeft?: () => void; activateById: (id: string) => void };
}

export async function openNotebook(
  app: LiteApp,
  path: string,
): Promise<NotebookPanel> {
  const panel = (await app.commands.execute("docmanager:open", {
    path,
    factory: "Notebook",
    kernelPreference: {
      name: "python",
      language: "python",
      autoStartDefault: true,
    },
  })) as NotebookPanel | undefined;
  if (!panel?.context?.model)
    throw new Error("JupyterLite could not open this notebook.");
  await panel.context.ready;
  app.shell.activateById(panel.id);
  return panel;
}

export async function runNotebook(app: LiteApp, path: string): Promise<void> {
  const panel = await openNotebook(app, path);
  const session = panel.context.sessionContext;
  if (session) {
    await withTimeout(
      (async () => {
        await session.initialize();
        if (!session.session?.kernel)
          await session.changeKernel({ name: "python" });
        await session.ready;
      })(),
      120_000,
      "Python did not start. Check your connection and reload the notebook.",
    );
  }
  if (!app.commands.hasCommand("notebook:run-all-cells"))
    throw new Error("The notebook execution command is unavailable.");
  const success = await app.commands.execute("notebook:run-all-cells", {
    widgetId: panel.id,
  });
  await panel.context.save();
  const cells = panel.context.model.toJSON().cells ?? [];
  const failure = cells
    .flatMap((cell) => cell.outputs ?? [])
    .find((output) => output.output_type === "error");
  if (failure)
    throw new Error(
      `${failure.ename ?? "Python error"}: ${failure.evalue ?? "Check the cell output."}`,
    );
  if (success !== true)
    throw new Error(
      "Execution stopped before every cell completed. Check the notebook and run it again.",
    );
}

export async function withTimeout<T>(
  task: Promise<T>,
  milliseconds: number,
  message: string,
): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      task,
      new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new Error(message)), milliseconds);
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}
