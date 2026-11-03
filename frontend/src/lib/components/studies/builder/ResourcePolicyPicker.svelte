<script lang="ts">
	/**
	 * ResourcePolicyPicker — the Budget preset step (spec 10.2 step 5): tier cards (Frugal / Standard /
	 * Generous, with their key numbers) and Custom (any preset the user can see), then the WP-14
	 * `ResourcePolicyEditor` for fine-grained knobs. Edits stay a draft until "Save as preset", which
	 * creates a user preset (or version+1 of the user's own) and pins it — presets are immutable.
	 * Class C/R knobs carry "affects treatment"; with `warnOnFingerprintChange` (a cloned or running
	 * study) the editor warns that the fingerprint changes.
	 *
	 * Props: `value` (the design's preset ref), `onchange(ref)`, `onsummary(line)` (the step's folded
	 * one-line summary), `warnOnFingerprintChange`, `disabled`.
	 */
	import type { LibraryItemOut, PolicyTier, ResourcePolicyContent } from '$lib/api/v3/types';
	import { POLICY_TIERS, parseRef } from '$lib/api/v3/types';
	import { formatApiError } from '$lib/api/core';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import { LibraryRefPicker, PinnedRefBadge, ResourcePolicyEditor, libraryStore, summaryLine, tierLabel } from '$lib/components/library';
	import { cn } from '$lib/utils';
	import { keyNumbers, sameContent, savePolicy } from './policy';
	import { PinnedPolicy, rememberPolicy } from './pinned.svelte';
	import Check from '@lucide/svelte/icons/check';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Save from '@lucide/svelte/icons/save';

	interface Props {
		value: string;
		onchange: (ref: string) => void;
		onsummary?: (line: string) => void;
		warnOnFingerprintChange?: boolean;
		disabled?: boolean;
	}

	let { value, onchange, onsummary, warnOnFingerprintChange = false, disabled = false }: Props = $props();

	const list = $derived(libraryStore.list('resource_policy'));
	const knobs = $derived(libraryStore.knobs);
	$effect(() => {
		void libraryStore.ensureKnobs();
		void libraryStore.ensureMe();
	});

	const tierRows = $derived(
		POLICY_TIERS.map((tier) => ({
			tier,
			item: list.items.find((i) => i.is_system && (i.columns?.tier === tier || i.slug === tier)) ?? null,
		})),
	);
	// The version `value` pins (not the slug's latest): the summary, the editor's base and "Save as preset"
	// all start from the knobs the run will use.
	const pinned = new PinnedPolicy(() => value);
	const current = $derived(pinned.item);
	const currentTier = $derived(
		(POLICY_TIERS as string[]).includes(String(current?.columns?.tier ?? '')) && current?.is_system ? (current?.columns?.tier as PolicyTier) : null,
	);

	let draft = $state<ResourcePolicyContent | null>(null);
	let draftFor = $state('');
	let saving = $state(false);
	let saveError = $state('');
	let presetName = $state('');
	let showEditor = $state(false);

	// The editor edits a copy of the chosen preset's content; switching presets drops an unsaved draft.
	$effect(() => {
		const item = current;
		if (item && draftFor !== item.pinned_ref) {
			draft = structuredClone(item.content as ResourcePolicyContent);
			draftFor = item.pinned_ref;
		}
	});

	const edited = $derived(!!draft && !!current && !sameContent(draft, current.content as ResourcePolicyContent));
	const line = $derived(
		draft && knobs
			? summaryLine(draft, {
					name: current?.name,
					version: current?.version,
					tier: (current?.columns?.tier as string) ?? null,
					base: (current?.content as ResourcePolicyContent) ?? null,
					tiers: knobs.tiers,
					knobs: knobs.knobs,
				}) + (edited ? ' · unsaved edits' : '')
			: current
				? `${current.name} v${current.version}`
				: value,
	);
	$effect(() => {
		onsummary?.(line);
	});

	function pick(item: LibraryItemOut | null) {
		if (!item || disabled) return;
		onchange(item.pinned_ref);
	}

	async function save() {
		if (!draft) return;
		saving = true;
		saveError = '';
		try {
			const saved = await savePolicy(current as LibraryItemOut<'resource_policy'> | null, draft, {
				name: presetName.trim() || undefined,
				ownUserId: libraryStore.me?.id ?? null,
				latestVersion: pinned.latest?.version ?? null,
			});
			rememberPolicy(saved);
			draftFor = saved.pinned_ref;
			draft = structuredClone(saved.content as ResourcePolicyContent);
			presetName = '';
			onchange(saved.pinned_ref);
		} catch (e) {
			saveError = formatApiError(e, 'Could not save the preset.');
		} finally {
			saving = false;
		}
	}
</script>

<div class="space-y-3">
	{#if pinned.error}<ErrorAlert message={pinned.error} />{/if}
	{#if list.error}
		<ErrorAlert message={list.error} onretry={() => list.revalidate()} />
	{:else if list.loading && list.items.length === 0}
		<LoadingState label="loading budget presets" />
	{:else}
		<div class="grid gap-2 sm:grid-cols-2 lg:grid-cols-4" role="radiogroup" aria-label="Budget preset">
			{#each tierRows as row (row.tier)}
				{@const active = currentTier === row.tier && !!current && current.slug === row.item?.slug}
				<button
					type="button"
					role="radio"
					aria-checked={active}
					disabled={disabled || !row.item}
					class={cn(
						'card-lift cursor-pointer space-y-1.5 rounded-md border border-border bg-card px-3 py-2.5 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-60',
						active && 'border-primary ring-1 ring-primary',
					)}
					onclick={() => pick(row.item)}
				>
					<span class="flex items-center gap-1.5 text-sm font-medium">
						{#if active}<Check class="size-3.5 text-primary" aria-hidden="true" />{/if}
						{tierLabel(row.tier)}
						{#if row.item}<span class="font-mono text-[11px] text-muted-foreground">v{row.item.version}</span>{/if}
					</span>
					{#if row.item}
						<span class="grid grid-cols-2 gap-x-2 gap-y-0.5 text-[11px]">
							{#each keyNumbers(row.item.content as ResourcePolicyContent) as n (n.label)}
								<span class="text-muted-foreground">{n.label}</span><span class="font-mono tabular-nums">{n.value}</span>
							{/each}
						</span>
					{:else}
						<span class="text-[11px] text-muted-foreground">not seeded</span>
					{/if}
				</button>
			{/each}
			<div class={cn('space-y-1.5 rounded-md border border-border bg-card px-3 py-2.5', current && !currentTier && 'border-primary ring-1 ring-primary')}>
				<span class="text-sm font-medium">Custom</span>
				<LibraryRefPicker
					kind="resource_policy"
					value={current && !currentTier ? [current.pinned_ref] : []}
					onchange={(refs) => refs[0] && onchange(refs[0])}
					{disabled}
					aria-label="Custom budget preset"
					placeholder="Any saved preset"
				/>
			</div>
		</div>

		{#if current}
			<div class="flex flex-wrap items-center gap-2 text-xs">
				<span class="text-muted-foreground">Pinned</span>
				<PinnedRefBadge ref={value.includes('#') ? value : current.pinned_ref} kind="resource_policy" />
				{#if parseRef(value) === null}<span class="text-muted-foreground">(pinned to the latest version when saved)</span>{/if}
				<Button variant="ghost" size="sm" class="ml-auto" aria-expanded={showEditor} onclick={() => (showEditor = !showEditor)}>
					{showEditor ? 'Hide knobs' : 'Edit knobs'}
				</Button>
			</div>
		{/if}

		{#if draft && knobs}
			<ResourcePolicyEditor
				value={draft}
				base={(current?.content as ResourcePolicyContent) ?? null}
				knobMeta={knobs.knobs}
				{warnOnFingerprintChange}
				keyLimitsOnly={!showEditor}
				readonly={disabled}
				onchange={(next) => (draft = next)}
			/>
		{:else if libraryStore.knobsError}
			<ErrorAlert message={libraryStore.knobsError} />
		{/if}

		{#if edited && !disabled}
			<div class="flex flex-wrap items-end gap-2 rounded-md border border-border bg-muted/30 px-3 py-2">
				<div class="space-y-1">
					<Label for="preset-name" class="text-xs">Preset name</Label>
					<Input id="preset-name" class="h-8 w-56 text-sm" placeholder={current ? `${current.name} (edited)` : 'Custom budget'} bind:value={presetName} />
				</div>
				<Button size="sm" onclick={save} disabled={saving}>
					{#if saving}<LoaderCircle class="animate-spin" />{:else}<Save />{/if}Save as preset
				</Button>
				<Button size="sm" variant="ghost" onclick={() => (draft = structuredClone(current!.content as ResourcePolicyContent))}>Discard edits</Button>
				<p class="w-full text-xs text-muted-foreground">Presets are immutable: saving creates a new preset (or a new version of yours) and pins it.</p>
			</div>
		{/if}
		{#if saveError}<ErrorAlert message={saveError} />{/if}
	{/if}
</div>
