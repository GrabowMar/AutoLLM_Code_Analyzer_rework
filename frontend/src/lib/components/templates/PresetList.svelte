<script lang="ts">
	/**
	 * PresetList — run presets of one kind (latest version per slug), with a
	 * per-preset version history. Harness presets are grouped by harness; a
	 * harness without tunable options offers no "New preset" (it would only
	 * duplicate its system default).
	 *
	 * Props: `kind`; `presets` (this kind only); `harnesses` (`/agent/config/`
	 * catalog, [] when unavailable); `loading`; `activeSlug` (row being edited);
	 * `onCreate(harnessName?)`, `onEdit`, `onDuplicate`, `onArchive`.
	 */
	import {
		getRunPresetVersions,
		type AgentHarnessInfo,
		type HarnessOptionField,
		type PresetKind,
		type RunPreset,
	} from '$lib/api/generation';
	import { formatApiError } from '$lib/api/core';
	import { formatDate } from '$lib/utils/formatters';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import { summarizePreset } from '$lib/components/generator';
	import { presetHarness, shortHash } from './presets';
	import Archive from '@lucide/svelte/icons/archive';
	import Copy from '@lucide/svelte/icons/copy';
	import History from '@lucide/svelte/icons/history';
	import Pencil from '@lucide/svelte/icons/pencil';
	import Plus from '@lucide/svelte/icons/plus';
	import SlidersHorizontal from '@lucide/svelte/icons/sliders-horizontal';

	interface Props {
		kind: PresetKind;
		presets: RunPreset[];
		harnesses: AgentHarnessInfo[];
		loading: boolean;
		activeSlug: string | null;
		onCreate: (harnessName?: string) => void;
		onEdit: (preset: RunPreset) => void;
		onDuplicate: (preset: RunPreset) => void;
		onArchive: (preset: RunPreset) => void;
	}

	let { kind, presets, harnesses, loading, activeSlug, onCreate, onEdit, onDuplicate, onArchive }: Props = $props();

	interface Group {
		key: string;
		harness: AgentHarnessInfo | null;
		presets: RunPreset[];
	}

	const groups = $derived.by((): Group[] => {
		if (kind !== 'harness' || harnesses.length === 0) return [{ key: 'all', harness: null, presets }];
		const ordered = [...harnesses].sort((a, b) => Number(b.is_default) - Number(a.is_default));
		const known: Group[] = ordered.map((h) => ({
			key: h.name,
			harness: h,
			presets: presets.filter((p) => presetHarness(p) === h.name),
		}));
		const other = presets.filter((p) => !harnesses.some((h) => h.name === presetHarness(p)));
		return other.length ? [...known, { key: 'other', harness: null, presets: other }] : known;
	});

	function schemaFor(preset: RunPreset): HarnessOptionField[] {
		return harnesses.find((h) => h.name === presetHarness(preset))?.option_schema ?? [];
	}

	// Duplicating a harness preset only makes sense when its harness has options.
	function canDuplicate(preset: RunPreset): boolean {
		if (preset.kind !== 'harness') return true;
		return harnesses.length === 0 || schemaFor(preset).length > 0;
	}

	// ── version history ────────────────────────────────────────────────
	let historyKey = $state<string | null>(null);
	let historyLoading = $state(false);
	let historyError = $state('');
	let historyVersions = $state<RunPreset[]>([]);

	async function toggleHistory(preset: RunPreset) {
		const key = `${preset.kind}/${preset.slug}`;
		if (historyKey === key) {
			historyKey = null;
			return;
		}
		historyKey = key;
		historyLoading = true;
		historyError = '';
		try {
			const versions = await getRunPresetVersions(preset.kind, preset.slug);
			if (historyKey === key) historyVersions = versions;
		} catch (e) {
			if (historyKey === key) {
				historyVersions = [];
				historyError = formatApiError(e, 'Could not load the versions.');
			}
		} finally {
			if (historyKey === key) historyLoading = false;
		}
	}
</script>

{#snippet presetRow(preset: RunPreset)}
	{@const key = `${preset.kind}/${preset.slug}`}
	{@const own = !preset.is_system}
	<div class="data-list-row" data-active={activeSlug === preset.slug}>
		<div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-4">
			<div class="min-w-0 flex-1 space-y-0.5">
				<div class="flex flex-wrap items-center gap-2">
					<span class="text-sm font-medium">{preset.name}</span>
					<span class="font-mono text-[11px] text-muted-foreground">{preset.slug}@v{preset.version}</span>
					{#if preset.is_default}
						<!-- Outline, not the tinted default variant: green-on-tint is under 4.5:1 in light mode. -->
						<Badge variant="outline" class="h-5 px-1.5 text-[11px] text-success-fg">Default</Badge>
					{:else if preset.is_system}
						<Badge variant="secondary" class="h-5 px-1.5 text-[11px]">System</Badge>
					{:else}
						<Badge variant="outline" class="h-5 px-1.5 text-[11px]">Yours</Badge>
					{/if}
					{#if preset.is_archived}<Badge variant="secondary" class="h-5 px-1.5 text-[11px]">Archived</Badge>{/if}
				</div>
				<p class="break-words font-mono text-xs text-foreground/90">{summarizePreset(preset, schemaFor(preset))}</p>
				{#if preset.description}
					<p class="cell-sub line-clamp-1 text-xs text-muted-foreground" title={preset.description}>{preset.description}</p>
				{/if}
			</div>
			<div class="flex shrink-0 flex-wrap items-center justify-end gap-0.5">
				{#if canDuplicate(preset)}
					<Button
						variant="ghost"
						size="sm"
						class="h-8 text-muted-foreground hover:text-foreground {own ? 'w-8 p-0' : 'px-2'}"
						title="Duplicate as a new preset"
						aria-label="Duplicate {preset.name}"
						onclick={() => onDuplicate(preset)}
					>
						<Copy />{#if !own}<span>Customize</span>{/if}
					</Button>
				{/if}
				<Button
					variant="ghost"
					size="sm"
					class="size-8 p-0 text-muted-foreground hover:text-foreground"
					title="Version history"
					aria-label="Version history of {preset.name}"
					aria-expanded={historyKey === key}
					onclick={() => toggleHistory(preset)}
				>
					<History />
				</Button>
				{#if own && !preset.is_archived}
					<Button
						variant="ghost"
						size="sm"
						class="size-8 p-0 text-muted-foreground hover:bg-destructive/10 hover:text-danger-fg"
						title="Archive every version (jobs keep their references)"
						aria-label="Archive {preset.name}"
						onclick={() => onArchive(preset)}
					>
						<Archive />
					</Button>
				{/if}
				{#if own && !preset.is_archived}
					<Button variant="ghost" size="sm" class="h-8 px-2" title="A config change saves a new version" onclick={() => onEdit(preset)}>
						<Pencil /> Edit
					</Button>
				{/if}
			</div>
		</div>

		{#if historyKey === key}
			<div class="mt-3 rounded-md border border-border bg-muted/20 px-3 py-2">
				{#if historyLoading}
					<LoadingState label="loading versions" class="py-2" />
				{:else if historyError}
					<p class="text-xs text-danger-fg">{historyError}</p>
				{:else}
					<ol class="space-y-1.5" aria-label="Versions of {preset.name}">
						{#each historyVersions as version (version.id)}
							<li class="flex flex-col gap-0.5 text-xs sm:flex-row sm:items-baseline sm:gap-3">
								<span class="flex shrink-0 items-center gap-1.5">
									<span class="font-mono text-foreground">v{version.version}</span>
									{#if version.version === preset.latest_version}<span class="text-[11px] text-success-fg">latest</span>{/if}
									{#if version.is_archived}<span class="text-[11px] text-muted-foreground">archived</span>{/if}
								</span>
								<span class="min-w-0 flex-1 break-words font-mono text-muted-foreground">
									{summarizePreset(version, schemaFor(version))}{#if version.name !== preset.name}<span class="font-sans"> · “{version.name}”</span>{/if}
								</span>
								<span class="shrink-0 font-mono text-[11px] text-muted-foreground" title="Content hash {version.content_hash}">
									#{shortHash(version.content_hash)}
								</span>
								<span class="shrink-0 text-[11px] text-muted-foreground">{formatDate(version.created_at)}</span>
							</li>
						{/each}
					</ol>
				{/if}
			</div>
		{/if}
	</div>
{/snippet}

{#if loading}
	<LoadingState label="loading presets" />
{:else if presets.length === 0 && (kind !== 'harness' || harnesses.length === 0)}
	<EmptyState icon={SlidersHorizontal} title="No {kind} presets" description="Create one, or import a starter package.">
		{#snippet action()}
			<Button size="sm" onclick={() => onCreate()}><Plus /> New {kind} preset</Button>
		{/snippet}
	</EmptyState>
{:else}
	<div class="space-y-5">
		{#each groups as group (group.key)}
			<section class="space-y-2" aria-label={group.harness ? `${group.harness.label} presets` : undefined}>
				{#if group.harness}
					<div class="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
						<div class="min-w-0">
							<p class="flex flex-wrap items-center gap-2 text-sm font-medium">
								{group.harness.label}
								<span class="font-mono text-[11px] font-normal text-muted-foreground">{group.harness.version}</span>
								{#if group.harness.is_default}<Badge variant="secondary">Default harness</Badge>{/if}
								{#if !group.harness.requires_tool_calling}<Badge variant="outline">No tool calling needed</Badge>{/if}
							</p>
							<p class="text-xs text-muted-foreground">
								{group.harness.description}
								{#if group.harness.option_schema.length === 0}
									No tunable options: every run uses its pinned configuration.
								{/if}
							</p>
						</div>
						{#if group.harness.option_schema.length > 0}
							<Button variant="outline" size="sm" class="shrink-0" onclick={() => onCreate(group.harness?.name)}>
								<Plus /> New {group.harness.label} preset
							</Button>
						{/if}
					</div>
				{:else if group.key === 'other'}
					<p class="section-label">Other harnesses (not available in this deployment)</p>
				{/if}
				{#if group.presets.length === 0}
					<p class="rounded-md border border-dashed px-3 py-2 text-xs text-muted-foreground">No presets for this harness.</p>
				{:else}
					<div class="data-list">
						{#each group.presets as preset (preset.id)}
							{@render presetRow(preset)}
						{/each}
					</div>
				{/if}
			</section>
		{/each}
	</div>
{/if}
