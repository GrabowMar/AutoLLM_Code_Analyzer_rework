<script lang="ts" module>
	export type PaneView = 'app' | 'spec' | 'source' | 'console';
</script>

<script lang="ts">
	/**
	 * AppPane — the workbench's left pane: the app viewer (one app, or A and B
	 * as tabs or side by side), the app's specification, its source files and
	 * the HTTP console (a drawer under the app on desktop, a tab on phones).
	 * On phones apps are not framed: each opens in a new tab.
	 *
	 * Props: `assignmentId`, `apps` (display order), `hosting` / `starting` (per
	 * slot), `decisions` (frameDecision per slot), `desktop`, `readOnly`,
	 * `consoleEnabled`, `dragging` (a pane resize is in progress), `sourceTarget`,
	 * bindable `focusSlot`, `sideBySide`, `view`, `consoleOpen`, and callbacks
	 * `onstart(slot)`, `onopen(slot)`, `onreadonly()`.
	 */
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import Monitor from '@lucide/svelte/icons/monitor';
	import PanelBottom from '@lucide/svelte/icons/panel-bottom';
	import RotateCw from '@lucide/svelte/icons/rotate-cw';
	import Smartphone from '@lucide/svelte/icons/smartphone';
	import Tablet from '@lucide/svelte/icons/tablet';
	import Columns2 from '@lucide/svelte/icons/columns-2';
	import X from '@lucide/svelte/icons/x';
	import type { HostingInfo, PortalApp } from '$lib/api/types/human-eval';
	import { Button } from '$lib/components/ui/button';
	import { Segmented } from '$lib/components/ui/segmented';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import { cn } from '$lib/utils';
	import AppFrame, { type FrameWidth } from './AppFrame.svelte';
	import HostingCard from './HostingCard.svelte';
	import HttpConsole from './HttpConsole.svelte';
	import SourceViewer from './SourceViewer.svelte';
	import SpecPanel from './SpecPanel.svelte';
	import { HOSTING_LABELS, hostingTones, idleHosting, type FrameDecision } from './hosting';
	import { slotTitle, type Slot } from './workbench';

	interface Props {
		assignmentId: string;
		apps: PortalApp[];
		hosting: Partial<Record<string, HostingInfo>>;
		starting: Partial<Record<string, boolean>>;
		decisions: Partial<Record<string, FrameDecision>>;
		desktop: boolean;
		readOnly?: boolean;
		consoleEnabled?: boolean;
		dragging?: boolean;
		sourceTarget?: { path: string; line: number | null; seq: number } | null;
		focusSlot: Slot;
		sideBySide: boolean;
		view: PaneView;
		consoleOpen: boolean;
		onstart: (slot: Slot) => void;
		onopen: (slot: Slot) => void;
		onreadonly?: () => void;
	}

	let {
		assignmentId,
		apps,
		hosting,
		starting,
		decisions,
		desktop,
		readOnly = false,
		consoleEnabled = false,
		dragging = false,
		sourceTarget = null,
		focusSlot = $bindable(),
		sideBySide = $bindable(),
		view = $bindable(),
		consoleOpen = $bindable(),
		onstart,
		onopen,
		onreadonly,
	}: Props = $props();

	let width = $state<FrameWidth>('full');
	let reloadKeys = $state<Record<string, number>>({});
	let consoleSlot = $state<Slot>('single');

	const pairwise = $derived(apps.length > 1);
	const focusApp = $derived(apps.find((a) => a.slot === focusSlot) ?? apps[0]);
	const hasSpec = $derived(apps.some((a) => !!a.spec || !!a.admin_credentials));
	const hasSource = $derived(apps.some((a) => a.has_source));
	const showBoth = $derived(desktop && pairwise && sideBySide && view === 'app');

	const viewItems = $derived([
		{ value: 'app', label: pairwise ? 'Apps' : 'App' },
		...(hasSpec ? [{ value: 'spec', label: 'Specification' }] : []),
		...(hasSource ? [{ value: 'source', label: 'Source' }] : []),
		...(consoleEnabled && !desktop ? [{ value: 'console', label: 'Console' }] : []),
	]);
	const widthItems = [
		{ value: 'full', label: 'Full width', icon: Monitor, title: 'Full width' },
		{ value: 'tablet', label: 'Tablet', icon: Tablet, title: 'Tablet width (820 px)' },
		{ value: 'phone', label: 'Phone', icon: Smartphone, title: 'Phone width (390 px)' },
	];

	// Keep the console pointed at the focused app until the participant picks another.
	$effect(() => {
		consoleSlot = focusSlot;
	});

	// Mount the source viewer and console the first time they are opened, then keep them.
	let sourceSeen = $state(false);
	let consoleSeen = $state(false);
	$effect(() => {
		if (view === 'source') sourceSeen = true;
		if (view === 'console' || consoleOpen) consoleSeen = true;
	});

	// A view that is not offered any more (e.g. resized to desktop) falls back to the app.
	$effect(() => {
		if (!viewItems.some((v) => v.value === view)) view = 'app';
	});

	function info(slot: string): HostingInfo {
		return hosting[slot] ?? idleHosting();
	}

	function decisionFor(slot: string): FrameDecision {
		return decisions[slot] ?? { canFrame: false, canOpen: false, url: null, reason: '' };
	}

	function reload(slot: string) {
		reloadKeys = { ...reloadKeys, [slot]: (reloadKeys[slot] ?? 0) + 1 };
	}
</script>

{#snippet frameActions(app: PortalApp, compact: boolean)}
	{@const d = decisionFor(app.slot)}
	{@const h = info(app.slot)}
	{#if h.state === 'running' && d.canFrame}
		<Button variant="ghost" size="icon" class="size-7" aria-label="Reload {slotTitle(app)}" title="Reload" onclick={() => reload(app.slot)}>
			<RotateCw class="size-3.5" />
		</Button>
	{/if}
	{#if h.state === 'running' && d.canOpen && d.url}
		<Button
			variant="ghost"
			size={compact ? 'icon' : 'sm'}
			class={compact ? 'size-7' : 'h-7 px-2 text-xs'}
			href={d.url}
			target="_blank"
			rel="noopener noreferrer"
			aria-label="Open {slotTitle(app)} in a new tab"
			title="Open in a new tab"
			onclick={() => onopen(app.slot)}
		>
			<ExternalLink class="size-3.5" />
			{#if !compact}<span class="hidden xl:inline">New tab</span>{/if}
		</Button>
	{/if}
{/snippet}

<section class="flex h-full min-h-0 min-w-0 flex-col overflow-hidden rounded-md border border-border bg-card shadow-[var(--shadow-card)]" aria-label="App viewer">
	<div class="flex flex-wrap items-center gap-2 border-b border-border px-2 py-1.5">
		{#if pairwise && !showBoth}
			<Segmented
				size="sm"
				aria-label="Which app"
				value={focusSlot}
				onValueChange={(v) => (focusSlot = v as Slot)}
				items={apps.map((a) => ({ value: a.slot, label: slotTitle(a) }))}
			/>
		{/if}
		{#if viewItems.length > 1}
			<Segmented
				size="sm"
				aria-label="Show"
				value={view}
				onValueChange={(v) => (view = v as PaneView)}
				items={viewItems}
			/>
		{/if}
		<div class="ml-auto flex items-center gap-1">
			{#if desktop && view === 'app'}
				{#if pairwise}
					<Button
						variant={sideBySide ? 'secondary' : 'ghost'}
						size="sm"
						class="h-7 px-2 text-xs"
						aria-pressed={sideBySide}
						title="Show A and B side by side"
						onclick={() => (sideBySide = !sideBySide)}
					>
						<Columns2 class="size-3.5" />
						<span class="hidden xl:inline">Side by side</span>
					</Button>
				{/if}
				<Segmented
					size="sm"
					aria-label="Frame width"
					value={width}
					onValueChange={(v) => (width = v as FrameWidth)}
					items={widthItems.map((w) => ({ ...w, label: '' }))}
				/>
				{#if !showBoth && focusApp}
					{@render frameActions(focusApp, false)}
				{/if}
			{/if}
			{#if desktop && consoleEnabled}
				<Button
					variant={consoleOpen ? 'secondary' : 'ghost'}
					size="sm"
					class="h-7 px-2 text-xs"
					aria-pressed={consoleOpen}
					aria-controls="workbench-console"
					onclick={() => (consoleOpen = !consoleOpen)}
				>
					<PanelBottom class="size-3.5" />
					Console
				</Button>
			{/if}
		</div>
	</div>

	<!-- Views stay mounted while hidden: the apps keep their page state, the console its request. -->
	<div class={cn('relative min-h-0 flex-1', view === 'app' && desktop ? 'overflow-hidden' : 'overflow-y-auto')}>
		<div class={desktop ? 'h-full' : ''} hidden={view !== 'app'}>
			{#if !desktop}
				<div class="space-y-3 p-3">
					<p class="text-xs text-muted-foreground">
						On a small screen {pairwise ? 'each app opens' : 'the app opens'} in a new tab. Come back here to record what you find; your answers save automatically.
					</p>
					{#each apps as app (app.slot)}
						<HostingCard
							{app}
							hosting={info(app.slot)}
							decision={{ ...decisionFor(app.slot), canFrame: false, reason: '' }}
							{readOnly}
							busy={!!starting[app.slot]}
							onstart={() => onstart(app.slot)}
							onopen={() => onopen(app.slot)}
							class="max-w-none"
						/>
					{/each}
				</div>
			{:else}
				<div class={cn('h-full min-h-0 p-2', showBoth && 'grid grid-cols-2 gap-2')}>
					{#each apps as app (app.slot)}
						<div class="flex h-full min-h-0 min-w-0 flex-col gap-1" hidden={!showBoth && app.slot !== focusApp?.slot}>
							{#if showBoth}
								<div class="flex items-center gap-1.5 px-1">
									<span class="min-w-0 truncate text-xs font-semibold">{slotTitle(app)}</span>
									<StatusBadge status={info(app.slot).state} tones={hostingTones} label={HOSTING_LABELS[info(app.slot).state]} />
									<span class="ml-auto flex items-center">{@render frameActions(app, true)}</span>
								</div>
							{/if}
							<AppFrame
								{app}
								hosting={info(app.slot)}
								decision={decisionFor(app.slot)}
								{width}
								reloadKey={reloadKeys[app.slot] ?? 0}
								{readOnly}
								busy={!!starting[app.slot]}
								onstart={() => onstart(app.slot)}
								onopen={() => onopen(app.slot)}
								class="min-h-0 flex-1"
							/>
						</div>
					{/each}
				</div>
				{#if dragging}
					<!-- Keeps the iframe from swallowing pointer events while the panes are resized. -->
					<div class="absolute inset-0 z-10 cursor-col-resize"></div>
				{/if}
			{/if}
		</div>
		{#if view === 'spec' && focusApp}
			<div class="p-3"><SpecPanel app={focusApp} /></div>
		{/if}
		{#if sourceSeen && focusApp}
			<div class="h-full p-2" hidden={view !== 'source'}>
				{#if focusApp.has_source}
					<SourceViewer {assignmentId} slot={focusApp.slot} label={focusApp.label} target={sourceTarget} />
				{:else}
					<p class="p-3 text-sm text-muted-foreground">No source files are shown for {focusApp.label}.</p>
				{/if}
			</div>
		{/if}
		{#if !desktop && consoleEnabled && consoleSeen}
			<div class="p-3" hidden={view !== 'console'}>
				<HttpConsole {assignmentId} {apps} bind:slot={consoleSlot} {hosting} {readOnly} {onreadonly} />
			</div>
		{/if}
	</div>

	{#if desktop && consoleEnabled && consoleSeen}
		<div id="workbench-console" class="flex h-[46%] min-h-[14rem] flex-col border-t border-border bg-surface-1" hidden={!consoleOpen}>
			<div class="flex items-center justify-between gap-2 border-b border-border px-3 py-1">
				<p class="text-xs font-semibold">HTTP console</p>
				<Button variant="ghost" size="icon" class="size-7" aria-label="Close the console" onclick={() => (consoleOpen = false)}>
					<X class="size-3.5" />
				</Button>
			</div>
			<div class="min-h-0 flex-1 px-3 py-2">
				<HttpConsole {assignmentId} {apps} bind:slot={consoleSlot} {hosting} {readOnly} {onreadonly} />
			</div>
		</div>
	{/if}
</section>
