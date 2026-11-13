<script lang="ts">
	/**
	 * ReportNotebook — the report's Notebook tab: the self-hosted JupyterLite site
	 * (`/jupyterlite/`, built by `frontend/jupyterlite/build.sh`) in a same-origin iframe. Once its
	 * app is up (`window.jupyterapp`, exposed by the build's config) this writes
	 * `reports/RPT-…/report.json` (always fresh) and `report.ipynb` (`buildNotebook`; only when
	 * missing, so the reader's edits survive — "Reset notebook" rewrites it), then opens the notebook
	 * in the app's theme. Python is Pyodide in the browser; files live in this browser's storage.
	 *
	 * Props: `report`, `doc` (the report's tables).
	 */
	import { mode } from 'mode-watcher';
	import { toast } from 'svelte-sonner';
	import Download from '@lucide/svelte/icons/download';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import { Button } from '$lib/components/ui/button';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import type { ReportDetail, ReportDocument } from '$lib/api/reports';
	import { buildNotebook, notebookData, notebookPaths } from './notebook';
	import { reportAccession } from './format';

	interface Props {
		report: ReportDetail;
		doc: ReportDocument;
	}

	let { report, doc }: Props = $props();

	const SRC = '/jupyterlite/lab/index.html';
	type Phase = 'loading' | 'writing' | 'ready' | 'error';
	let phase = $state<Phase>('loading');
	let error = $state('');
	let frame = $state<HTMLIFrameElement | null>(null);
	let confirmReset = $state(false);
	const paths = $derived(notebookPaths(report));

	// The JupyterLab application object (only the parts used here).
	interface LiteApp {
		started: Promise<void>;
		restored: Promise<void>;
		serviceManager: {
			contents: {
				get: (path: string, opts?: { content?: boolean }) => Promise<unknown>;
				save: (path: string, model: Record<string, unknown>) => Promise<unknown>;
			};
		};
		commands: { execute: (id: string, args?: Record<string, unknown>) => Promise<unknown>; hasCommand: (id: string) => boolean };
		shell: { mode?: string; collapseLeft?: () => void };
	}

	async function liteApp(timeoutMs = 90_000): Promise<LiteApp> {
		const t0 = Date.now();
		for (;;) {
			const app = (frame?.contentWindow as (Window & { jupyterapp?: LiteApp }) | null)?.jupyterapp;
			if (app) {
				await app.started;
				await app.restored;
				return app;
			}
			if (Date.now() - t0 > timeoutMs) throw new Error('JupyterLite did not start. Is the site built (frontend/jupyterlite/build.sh)?');
			await new Promise((r) => setTimeout(r, 250));
		}
	}

	async function exists(app: LiteApp, path: string) {
		try {
			await app.serviceManager.contents.get(path, { content: false });
			return true;
		} catch {
			return false;
		}
	}

	async function prepare(reset = false) {
		phase = 'writing';
		error = '';
		try {
			const app = await liteApp();
			const c = app.serviceManager.contents;
			for (const dir of ['reports', paths.dir]) if (!(await exists(app, dir))) await c.save(dir, { type: 'directory' });
			await c.save(paths.data, { type: 'file', format: 'text', content: JSON.stringify(notebookData(report, doc)) });
			if (reset || !(await exists(app, paths.notebook))) {
				await c.save(paths.notebook, { type: 'notebook', format: 'json', content: buildNotebook(report, doc, location.origin) });
			}
			const theme = mode.current === 'light' ? 'JupyterLab Light' : 'JupyterLab Dark';
			if (app.commands.hasCommand('apputils:change-theme')) await app.commands.execute('apputils:change-theme', { theme }).catch(() => {});
			app.shell.collapseLeft?.();
			await app.commands.execute('docmanager:open', { path: paths.notebook, factory: 'Notebook' });
			phase = 'ready';
			if (reset) toast.success('Notebook reset to the generated version');
		} catch (e) {
			phase = 'error';
			error = (e as Error)?.message || 'Could not open the notebook';
		}
	}

	function download(name: string, text: string, type: string) {
		const a = document.createElement('a');
		a.href = URL.createObjectURL(new Blob([text], { type }));
		a.download = name;
		a.click();
		setTimeout(() => URL.revokeObjectURL(a.href), 1000);
	}

</script>

<section aria-label="Notebook" class="overflow-clip rounded-md border border-border bg-card shadow-[var(--shadow-card)]">
	<div class="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border/60 px-4 py-2.5">
		<div class="min-w-0 flex-1">
			<p class="text-sm font-medium">
				Notebook <span class="font-mono text-xs font-normal text-muted-foreground">{paths.notebook}</span>
			</p>
			<p class="text-xs text-muted-foreground" role="status" aria-live="polite">
				{#if phase === 'loading'}
					<LoaderCircle class="mr-1 inline size-3 animate-spin align-[-2px] motion-reduce:animate-none" />Starting JupyterLite… the first start downloads Python (~30 MB, then cached).
				{:else if phase === 'writing'}
					<LoaderCircle class="mr-1 inline size-3 animate-spin align-[-2px] motion-reduce:animate-none" />Writing the report into the notebook…
				{:else if phase === 'ready'}
					Python runs in your browser. <code>T[1]</code>…<code>T[{doc.tables.length}]</code> are the tables as DataFrames; edits stay in this browser.
				{:else}
					Could not open the notebook.
				{/if}
			</p>
		</div>
		<div class="flex flex-wrap items-center gap-2">
			<Button variant="outline" size="sm" disabled={phase !== 'ready'} onclick={() => (confirmReset = true)} title="Replace the notebook with a freshly generated one">
				<RotateCcw class="size-3.5" />Reset
			</Button>
			<Button
				variant="outline"
				size="sm"
				onclick={() => download(`${reportAccession(report.report_id).toLowerCase()}.ipynb`, JSON.stringify(buildNotebook(report, doc, location.origin), null, 1), 'application/x-ipynb+json')}
				title="The generated notebook, for Jupyter, VS Code or Colab (put report.json next to it)"
			>
				<Download class="size-3.5" />.ipynb
			</Button>
			<Button variant="outline" size="sm" onclick={() => download('report.json', JSON.stringify(notebookData(report, doc), null, 1), 'application/json')} title="The data the notebook loads">
				<Download class="size-3.5" />report.json
			</Button>
			<Button variant="outline" size="sm" href={SRC} target="_blank" rel="noopener" title="Open JupyterLite in a new tab (same files)">
				<ExternalLink class="size-3.5" />Full screen
			</Button>
		</div>
	</div>
	{#if phase === 'error'}
		<div class="p-3"><ErrorAlert message={error} onretry={() => prepare()} /></div>
	{/if}
	<iframe
		bind:this={frame}
		src={SRC}
		title="JupyterLite notebook for {report.title}"
		class="block h-[max(36rem,calc(100dvh-12rem))] w-full border-0 bg-background"
		onload={() => prepare()}
	></iframe>
</section>

<ConfirmDialog
	bind:open={confirmReset}
	title="Reset the notebook?"
	description="Your edits to {paths.notebook} in this browser are replaced with the generated notebook. report.json is refreshed either way."
	confirmLabel="Reset"
	destructive
	onConfirm={() => {
		confirmReset = false;
		void prepare(true);
	}}
/>
