<script lang="ts">
	import { untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { MediaQuery } from 'svelte/reactivity';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import { Alert, AlertDescription } from '$lib/components/ui/alert';
	import { Button } from '$lib/components/ui/button';
	import { Segmented } from '$lib/components/ui/segmented';
	import { OpenRouterKeyBanner, type OpenRouterKeyState } from '$lib/components/generator';
	import {
		AgentComposer,
		ScaffoldingComposer,
		SinglePromptComposer,
		prepareComposer,
		storedComposer,
		type ComposerMode,
		type ComposerSource,
		type PreparedComposer,
	} from '$lib/components/generator/composer';
	import Bot from '@lucide/svelte/icons/bot';
	import Info from '@lucide/svelte/icons/info';
	import Layers from '@lucide/svelte/icons/layers';
	import Library from '@lucide/svelte/icons/library';
	import MessageSquareCode from '@lucide/svelte/icons/message-square-code';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import X from '@lucide/svelte/icons/x';

	let { data } = $props();

	const MODES = {
		agent: {
			label: 'Agent',
			icon: Bot,
			badge: 'Recommended',
			description: 'An agent harness builds each app in a sandbox; app specs are then graded by hidden tests.',
		},
		scaffolding: {
			label: 'Template scaffolding',
			icon: Layers,
			badge: undefined,
			description: 'A prompt profile fills a stack template in one pass per app spec and model.',
		},
		custom: {
			label: 'Single prompt',
			icon: MessageSquareCode,
			badge: undefined,
			description: 'One raw system and user prompt to one model; nothing is added or graded.',
		},
	} as const;
	// Below `sm` the three full labels don't fit one row: short labels, no badge.
	const wide = new MediaQuery('min-width: 640px', true);
	const SHORT_LABELS: Record<ComposerMode, string> = {
		agent: 'Agent',
		scaffolding: 'Scaffolding',
		custom: 'Single prompt',
	};
	const modeItems = $derived(
		(Object.keys(MODES) as ComposerMode[]).map((value) => ({
			value,
			label: wide.current ? MODES[value].label : SHORT_LABELS[value],
			icon: MODES[value].icon,
			badge: wide.current ? MODES[value].badge : undefined,
			title: MODES[value].description,
		}))
	);

	let prepared = $state<PreparedComposer | null>(null);
	let preparing = $state(false);
	let keyState = $state<OpenRouterKeyState>('loading');
	let dismissedFor = $state<PreparedComposer | null>(null);

	// Each prepare call gets a token; a newer URL or mode switch supersedes it.
	let token = 0;
	// The URL state the composer currently reflects (consumed params excluded).
	let appliedKey = '';

	const mode = $derived<ComposerMode>(prepared?.mode ?? data.mode);
	const showMessages = $derived(!!prepared && dismissedFor !== prepared && !!(prepared.notice || prepared.warning));

	function sourceKey(source: ComposerSource): string {
		return JSON.stringify([source.mode, source.modelSlug, source.appSlug, source.fromJobId]);
	}

	// One-shot params (?model, ?app, ?from_job) are consumed: the URL keeps only
	// the mode, and the draft (sessionStorage) carries the selection from here on.
	function settleUrl(next: ComposerMode) {
		appliedKey = sourceKey({ mode: next, modelSlug: null, appSlug: null, fromJobId: null });
		const search = page.url.search;
		if (search === `?mode=${next}` || (search === '' && next === 'agent')) return;
		void goto(`/sample-generator?mode=${next}`, { replaceState: true, noScroll: true, keepFocus: true });
	}

	async function open(source: ComposerSource, invalidFromJob: string | null) {
		const mine = ++token;
		if (!source.fromJobId && !source.modelSlug && !source.appSlug) {
			preparing = false;
			const stored = storedComposer(source.mode);
			prepared = invalidFromJob ? { ...stored, warning: `“${invalidFromJob}” is not a job id.` } : stored;
			settleUrl(source.mode);
			return;
		}
		preparing = true;
		try {
			const result = await prepareComposer(source);
			if (mine !== token) return;
			prepared = result;
			settleUrl(result.mode);
		} finally {
			if (mine === token) preparing = false;
		}
	}

	$effect(() => {
		const source: ComposerSource = {
			mode: data.mode,
			modelSlug: data.modelSlug,
			appSlug: data.appSlug,
			fromJobId: data.fromJobId,
		};
		const invalidFromJob = data.invalidFromJob;
		const key = sourceKey(source);
		if (key === appliedKey) return;
		appliedKey = key;
		untrack(() => void open(source, invalidFromJob));
	});

	function switchMode(next: ComposerMode) {
		if (prepared?.mode === next && !preparing) return;
		token += 1;
		preparing = false;
		prepared = storedComposer(next);
		settleUrl(next);
	}
</script>

<div class="space-y-6">
	<PageHeader
		title="Sample Generator"
		description="Compose generation runs, check exactly what will run and what it may cost, then start them."
	>
		{#snippet actions()}
			<Button href="/sample-generator/batches" variant="outline" size="sm"><Layers />Batches</Button>
			<Button href="/templates" variant="outline" size="sm"><Library />Library</Button>
		{/snippet}
		<p class="max-w-3xl text-xs text-muted-foreground">
			<span class="font-medium text-foreground">App spec</span>: the task, graded by hidden tests ·
			<span class="font-medium text-foreground">Harness</span>: the agent program that drives the model ·
			<span class="font-medium text-foreground">Preset</span>: saved, versioned harness, sampling or budget settings ·
			<span class="font-medium text-foreground">Stack</span>: the app skeleton
		</p>
	</PageHeader>

	<div class="space-y-3">
		<div class="flex flex-col gap-2 md:flex-row md:items-center md:gap-4">
			<Segmented
				aria-label="Generation mode"
				value={mode}
				items={modeItems}
				class="self-start"
				onValueChange={(v) => switchMode(v as ComposerMode)}
			/>
			<p class="min-w-0 text-xs text-muted-foreground">
				{#if mode === 'agent' && !wide.current}<span class="font-medium text-success-fg">Recommended.</span>{/if}
				{MODES[mode].description}
			</p>
		</div>
		<OpenRouterKeyBanner bind:keyState />
	</div>

	{#if showMessages && prepared}
		<div class="space-y-2">
			{#if prepared.notice}
				<Alert class="border-info/30 bg-info/10" role="status">
					<Info class="text-info-fg" />
					<div class="flex items-start justify-between gap-2">
						<AlertDescription class="min-w-0 text-foreground">{prepared.notice}</AlertDescription>
						<Button
							variant="ghost"
							size="icon"
							class="size-6 shrink-0"
							aria-label="Dismiss"
							onclick={() => (dismissedFor = prepared)}><X /></Button
						>
					</div>
				</Alert>
			{/if}
			{#if prepared.warning}
				<Alert class="border-warning/30 bg-warning/10">
					<TriangleAlert class="text-warning-fg" />
					<div class="flex items-start justify-between gap-2">
						<AlertDescription class="min-w-0 text-warning-fg">{prepared.warning}</AlertDescription>
						<Button
							variant="ghost"
							size="icon"
							class="size-6 shrink-0"
							aria-label="Dismiss"
							onclick={() => (dismissedFor = prepared)}><X /></Button
						>
					</div>
				</Alert>
			{/if}
		</div>
	{/if}

	{#if preparing || !prepared}
		<LoadingState label={data.fromJobId ? 'loading the job’s settings' : 'loading'} />
	{:else}
		{#key prepared}
			{#if prepared.mode === 'agent'}
				<AgentComposer initial={prepared.draft} pins={prepared.pins} />
			{:else if prepared.mode === 'scaffolding'}
				<ScaffoldingComposer initial={prepared.draft} pins={prepared.pins} {keyState} />
			{:else}
				<SinglePromptComposer initial={prepared.draft} pins={prepared.pins} {keyState} />
			{/if}
		{/key}
	{/if}
</div>
