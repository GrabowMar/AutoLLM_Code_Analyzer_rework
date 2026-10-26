<script lang="ts">
	/**
	 * New analysis: pick generated apps (one run each, queued as a batch) or
	 * paste files, pick workspace tools, optionally name it, and start. A free
	 * preview says, per tool, where it would actually run (files it reads, app
	 * container running for dynamic tools) before anything is created.
	 *
	 * URL: `?generation_job_id=<id>` / repeated `?job=<id>` preselect apps,
	 * `?tools=a,b` preselects tools. The last tool selection is remembered.
	 */
	import { onDestroy, onMount, untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { toast } from 'svelte-sonner';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Segmented } from '$lib/components/ui/segmented';
	import AppWindow from '@lucide/svelte/icons/app-window';
	import ClipboardPaste from '@lucide/svelte/icons/clipboard-paste';
	import Play from '@lucide/svelte/icons/play';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import ScanSearch from '@lucide/svelte/icons/scan-search';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ComposerSection from '$lib/components/generator/composer/ComposerSection.svelte';
	import RunFormula, { type RunFactor } from '$lib/components/generator/composer/RunFormula.svelte';
	import StartBar from '$lib/components/generator/composer/StartBar.svelte';
	import ComposerSectionNav, { type ComposerNavSection } from '$lib/components/experiments/ComposerSectionNav.svelte';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import type { AnalyzerTool } from '$lib/api/analyzers';
	import { getGenerationJob } from '$lib/api/generation';
	import { createBatch, createRun, previewRuns, type RunPreview } from '$lib/api/runs';
	import AppPicker, { MAX_APPS, toPicked, type PickedApp } from '$lib/components/analysis/AppPicker.svelte';
	import ToolPicker from '$lib/components/analysis/ToolPicker.svelte';
	import CodeFilesEditor, { newFile, toSourceCode, type CodeFile } from '$lib/components/analysis/CodeFilesEditor.svelte';
	import { loadTools } from '$lib/components/analysis/tools';

	const LAST_TOOLS_KEY = 'analysis.lastTools';

	// ── state ─────────────────────────────────────────────────────────
	let mode = $state<'apps' | 'code'>('apps');
	let apps = $state<PickedApp[]>([]);
	let running = $state(new Set<string>());
	let files = $state<CodeFile[]>([newFile('app.py'), newFile('requirements.txt')]);
	let name = $state('');

	let tools = $state<AnalyzerTool[]>([]);
	let toolsLoading = $state(true);
	let toolsError = $state('');
	let selectedTools = $state<string[]>([]);
	let toolsTouched = false;

	let preview = $state<RunPreview | null>(null);
	let previewLoading = $state(false);
	let previewError = $state('');
	let launching = $state(false);
	let launchError = $state('');

	const sourceCode = $derived(toSourceCode(files));
	const codeFiles = $derived(Object.keys(sourceCode).length);
	const targets = $derived(mode === 'apps' ? apps.length : codeFiles > 0 ? 1 : 0);
	const codeInvalid = $derived(
		mode === 'code' && files.some((f) => f.content.trim() && !f.path.trim())
	);

	// ── tools ─────────────────────────────────────────────────────────
	function usable(t: AnalyzerTool) {
		return t.installed && t.is_enabled && !(mode === 'code' && t.analysis_mode === 'dynamic');
	}

	function initialTools(list: AnalyzerTool[]): string[] {
		const fromUrl = page.url.searchParams.get('tools');
		let wanted: string[] | null = fromUrl ? fromUrl.split(',') : null;
		if (!wanted) {
			try {
				const raw = localStorage.getItem(LAST_TOOLS_KEY);
				if (raw) wanted = JSON.parse(raw) as string[];
			} catch {
				// No stored selection.
			}
		}
		const ok = list.filter((t) => t.installed && t.is_enabled);
		if (wanted) {
			const set = new Set(wanted);
			const picked = ok.filter((t) => set.has(t.slug)).map((t) => t.slug);
			if (picked.length > 0) return picked;
		}
		// Default: every installed tool except the AI reviewer (it costs tokens).
		return ok.filter((t) => t.kind !== 'ai').map((t) => t.slug);
	}

	async function fetchTools(force = false) {
		toolsError = '';
		try {
			tools = await loadTools(force);
			if (!toolsTouched) selectedTools = initialTools(tools);
			else selectedTools = selectedTools.filter((s) => tools.some((t) => t.slug === s && t.installed));
		} catch (e) {
			toolsError = formatApiError(e, 'Could not load the tool catalog.');
		} finally {
			toolsLoading = false;
		}
	}

	// Switching to pasted code drops dynamic tools (no running app to probe).
	$effect(() => {
		void mode;
		untrack(() => {
			const allowed = selectedTools.filter((s) => {
				const t = tools.find((x) => x.slug === s);
				return !t || usable(t);
			});
			if (allowed.length !== selectedTools.length) selectedTools = allowed;
		});
	});

	function onToolsChange(next: string[]) {
		toolsTouched = true;
		selectedTools = next;
		try {
			localStorage.setItem(LAST_TOOLS_KEY, JSON.stringify(next));
		} catch {
			// Storage unavailable: the selection just isn't remembered.
		}
	}

	// ── preselected apps ──────────────────────────────────────────────
	async function preselect() {
		const params = page.url.searchParams;
		const ids = [...new Set([...params.getAll('job'), ...params.getAll('generation_job_id')])].filter(Boolean).slice(0, MAX_APPS);
		if (ids.length === 0) return;
		const found = await Promise.allSettled(ids.map((id) => getGenerationJob(id)));
		const picked = found.flatMap((r) => (r.status === 'fulfilled' ? [toPicked(r.value)] : []));
		apps = picked;
		const missing = found.length - picked.length;
		if (missing > 0) toast.error(`${missing} linked app${missing === 1 ? '' : 's'} could not be loaded`);
	}

	// ── preview ───────────────────────────────────────────────────────
	let previewCtl: AbortController | null = null;
	let previewTimer: ReturnType<typeof setTimeout> | undefined;

	const previewKey = $derived(
		JSON.stringify({ mode, apps: apps.map((a) => a.id), tools: selectedTools, code: mode === 'code' ? sourceCode : null })
	);

	async function runPreview() {
		previewCtl?.abort();
		if (targets === 0 || selectedTools.length === 0) {
			preview = null;
			previewLoading = false;
			return;
		}
		const ctl = new AbortController();
		previewCtl = ctl;
		previewLoading = true;
		try {
			preview = await previewRuns(
				mode === 'apps'
					? { tool_slugs: selectedTools, generation_job_ids: apps.map((a) => a.id) }
					: { tool_slugs: selectedTools, source_code: sourceCode },
				{ signal: ctl.signal }
			);
			previewError = '';
		} catch (e) {
			if (isAbortError(e) && ctl.signal.aborted) return;
			previewError = formatApiError(e, 'Could not preview the run.');
		} finally {
			if (previewCtl === ctl) previewLoading = false;
		}
	}

	$effect(() => {
		void previewKey;
		untrack(() => {
			clearTimeout(previewTimer);
			previewTimer = setTimeout(() => void runPreview(), 400);
		});
	});

	// ── lifecycle ─────────────────────────────────────────────────────
	function onFocus() {
		// Tools may have been installed in the Analyzers tab meanwhile.
		void fetchTools(true);
	}

	onMount(() => {
		void fetchTools();
		void preselect();
		window.addEventListener('focus', onFocus);
	});
	onDestroy(() => {
		window.removeEventListener('focus', onFocus);
		clearTimeout(previewTimer);
		previewCtl?.abort();
	});

	// ── summary & launch ──────────────────────────────────────────────
	const selectedUsable = $derived(selectedTools.filter((s) => tools.some((t) => t.slug === s && usable(t))));
	const toolRuns = $derived(preview?.tool_runs ?? null);
	const skipped = $derived(toolRuns == null ? 0 : targets * selectedUsable.length - toolRuns);

	const blockers = $derived(
		[
			targets === 0 && (mode === 'apps' ? 'Select at least one app' : 'Paste at least one named file'),
			codeInvalid && 'Every pasted file needs a path',
			selectedUsable.length === 0 && 'Select at least one installed tool',
		].filter(Boolean) as string[]
	);
	const canStart = $derived(blockers.length === 0 && !launching);

	const factors = $derived<RunFactor[]>([
		{ label: mode === 'apps' ? 'apps' : 'code sets', singular: mode === 'apps' ? 'app' : 'code set', count: targets, href: '#target' },
		{ label: 'tools', singular: 'tool', count: selectedUsable.length, href: '#tools' },
	]);

	const navSections = $derived<ComposerNavSection[]>([
		{ id: 'target', step: 1, label: 'What to analyze', done: targets > 0 && !codeInvalid, invalid: codeInvalid },
		{ id: 'tools', step: 2, label: 'Tools', done: selectedUsable.length > 0 },
		{ id: 'options', step: 3, label: 'Name' },
	]);

	async function start() {
		if (!canStart) return;
		launching = true;
		launchError = '';
		try {
			if (mode === 'apps' && apps.length > 1) {
				const res = await createBatch({ name: name.trim() || undefined, tool_slugs: selectedUsable, generation_job_ids: apps.map((a) => a.id) });
				toast.success(`${res.run_ids.length} analyses queued`);
				await goto(`/analysis?batch=${res.batch_id}`);
			} else {
				const run = await createRun({
					name: name.trim() || undefined,
					tool_slugs: selectedUsable,
					...(mode === 'apps' ? { generation_job_id: apps[0].id } : { source_code: sourceCode }),
				});
				toast.success('Analysis started');
				await goto(`/analysis/${run.id}?tab=tools`);
			}
		} catch (e) {
			launchError = formatApiError(e, 'Could not start the analysis.');
			toast.error(launchError);
		} finally {
			launching = false;
		}
	}

	const workspaceNote = $derived.by(() => {
		const s = preview?.workspace_status;
		if (!s || s === 'ready') return '';
		if (s === 'provisioning') return 'Your analyzer workspace is being created; the run waits for it.';
		return 'Your analyzer workspace is not running; it starts automatically when the run begins (about a minute).';
	});
</script>

{#snippet summary()}
	<Card.Root class="gap-3">
		<Card.Header>
			<Card.Title class="text-base">Run summary</Card.Title>
			<Card.Description class="text-xs">
				{mode === 'apps' && apps.length > 1 ? 'One run per app, queued one after another in your workspace.' : 'One run in your analyzer workspace.'}
			</Card.Description>
		</Card.Header>
		<Card.Content class="space-y-3">
			<RunFormula {factors} total={targets && selectedUsable.length ? (toolRuns ?? targets * selectedUsable.length) : null} unit="tool runs" />
			{#if previewLoading}
				<p class="flex items-center gap-1.5 text-xs text-muted-foreground"><LoaderCircle class="size-3 animate-spin motion-reduce:animate-none" />Checking which tools apply…</p>
			{:else if previewError}
				<p class="text-xs text-danger-fg">{previewError}</p>
			{:else if preview && skipped > 0}
				<p class="text-xs text-muted-foreground">
					{skipped} tool run{skipped === 1 ? '' : 's'} would be skipped (nothing to read, or the app isn't running) — they're recorded with the reason, never as clean results.
				</p>
			{/if}
			{#if preview && mode === 'apps' && preview.targets.length > 0}
				{@const up = preview.targets.filter((t) => t.app_running).length}
				{@const dynamic = preview.tools.filter((t) => t.mode === 'dynamic').length}
				{#if dynamic > 0}
					<p class="text-xs {up === preview.targets.length ? 'text-muted-foreground' : 'text-warning-fg'}">
						{up}/{preview.targets.length} app{preview.targets.length === 1 ? '' : 's'} running for the {dynamic} dynamic tool{dynamic === 1 ? '' : 's'}.
					</p>
				{/if}
				<p class="text-xs text-muted-foreground">
					{preview.targets.reduce((n, t) => n + t.files, 0)} files · {preview.targets.reduce((n, t) => n + t.lines, 0).toLocaleString()} lines to scan.
				</p>
			{/if}
			{#each preview?.warnings ?? [] as w (w)}
				<p class="flex items-start gap-1.5 text-xs text-warning-fg"><TriangleAlert class="mt-0.5 size-3 shrink-0" />{w}</p>
			{/each}
			{#if workspaceNote}<p class="text-xs text-muted-foreground">{workspaceNote}</p>{/if}
			{#if launchError}<ErrorAlert message={launchError} />{/if}
			<div class="space-y-1.5 border-t border-border pt-3">
				<Button class="w-full" disabled={!canStart} onclick={start}>
					{#if launching}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{:else}<Play />{/if}
					{mode === 'apps' && apps.length > 1 ? `Start ${apps.length} analyses` : 'Start analysis'}
				</Button>
				{#if blockers.length > 0}
					<ul class="space-y-0.5 text-xs text-muted-foreground">{#each blockers as b (b)}<li>· {b}</li>{/each}</ul>
				{:else}
					<p class="text-xs text-muted-foreground">You can leave the page; progress is on the run's page and in the list.</p>
				{/if}
			</div>
		</Card.Content>
	</Card.Root>
{/snippet}

<div class="space-y-4">
	<PageHeader title="New analysis" description="Pick apps (or paste code) and tools; preview where each tool runs, then start." back={{ href: '/analysis', label: 'Analysis' }}>
		{#snippet actions()}
			<Button variant="outline" size="sm" href="/analyzers"><ScanSearch />Analyzers</Button>
		{/snippet}
	</PageHeader>

	<div class="@container/composer">
		<div class="grid items-start gap-4 @4xl/composer:grid-cols-[minmax(0,1fr)_22rem] @6xl/composer:grid-cols-[minmax(0,1fr)_24rem]">
			<div class="min-w-0 space-y-4">
				<ComposerSection
					step={1}
					id="target"
					title="What to analyze"
					description={mode === 'apps' ? 'Completed generated apps — each gets its own run. Dynamic tools need the app running.' : 'Files pasted here; dynamic tools need a generated app.'}
					done={targets > 0 && !codeInvalid}
					invalid={codeInvalid}
					meta={mode === 'apps' ? `${apps.length} selected` : `${codeFiles} file${codeFiles === 1 ? '' : 's'}`}
				>
					<Segmented
						aria-label="Source"
						value={mode}
						onValueChange={(v) => (mode = v as typeof mode)}
						items={[
							{ value: 'apps', label: 'Generated apps', icon: AppWindow },
							{ value: 'code', label: 'Paste code', icon: ClipboardPaste },
						]}
					/>
					{#if mode === 'apps'}
						<AppPicker bind:selected={apps} bind:running />
					{:else}
						<CodeFilesEditor bind:files />
					{/if}
				</ComposerSection>

				<ComposerSection
					step={2}
					id="tools"
					title="Tools"
					description="Tools installed in your analyzer workspace. They run one after another; findings keep the level each tool printed."
					done={selectedUsable.length > 0}
					meta="{selectedUsable.length} selected"
				>
					{#snippet actions()}
						<Button variant="ghost" size="sm" class="h-7 text-xs" onclick={() => fetchTools(true)} title="Reload the catalog (after installing tools)">
							<RefreshCw class="size-3.5" />Reload
						</Button>
					{/snippet}
					{#if toolsLoading}
						<LoadingState label="loading tools" />
					{:else if toolsError}
						<ErrorAlert message={toolsError} onretry={() => fetchTools(true)} />
					{:else if !tools.some((t) => t.installed)}
						<EmptyState compact icon={ScanSearch} title="No tools installed" description="Install analyzers into your workspace first.">
							{#snippet action()}<Button size="sm" href="/analyzers">Open Analyzers</Button>{/snippet}
						</EmptyState>
					{:else}
						<ToolPicker {tools} bind:selected={() => selectedTools, onToolsChange} {preview} {targets} codeMode={mode === 'code'} />
					{/if}
				</ComposerSection>

				<ComposerSection step={3} id="options" title="Name" description="Optional. Defaults to the app and model (or “Pasted code”).">
					<Input bind:value={name} placeholder={mode === 'apps' && apps.length === 1 ? `${apps[0].app || apps[0].stack} · ${apps[0].model}` : 'e.g. Baseline scan'} maxlength={200} class="max-w-md" aria-label="Run name" />
				</ComposerSection>

				<div class="@4xl/composer:hidden" id="summary">{@render summary()}</div>
			</div>

			<aside class="hidden min-w-0 @4xl/composer:sticky @4xl/composer:top-[4.5rem] @4xl/composer:block @4xl/composer:max-h-[calc(100dvh-5.5rem)] @4xl/composer:overflow-y-auto @4xl/composer:overscroll-contain" aria-label="Run summary">
				<div class="mb-3"><ComposerSectionNav sections={navSections} /></div>
				{@render summary()}
			</aside>
		</div>
		<StartBar
			runs={mode === 'apps' ? apps.length : targets}
			errors={codeInvalid ? 1 : 0}
			label={mode === 'apps' && apps.length > 1 ? `Start ${apps.length}` : 'Start'}
			{canStart}
			busy={launching}
			reason={blockers[0] ?? ''}
			summaryHref="#summary"
			onstart={start}
		/>
	</div>
</div>
