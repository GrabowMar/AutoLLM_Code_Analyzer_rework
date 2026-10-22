<script lang="ts">
	/**
	 * ProfilePicker — prompt profile for template scaffolding.
	 *
	 * "Auto" (selectedId = null) resolves a profile per app on the server
	 * (app pilot → stack default → system default); the picker shows what Auto
	 * resolves to for the selected apps, and can render the exact prompts.
	 *
	 * Props:
	 * - `selectedId` (bindable `number | null`): profile pk, or null for Auto.
	 * - `profiles`: preloaded list (otherwise fetched on mount).
	 * - `appIds`: selected app spec pks — drives the Auto suggestions (debounced).
	 * - `stackSlug`: scopes suggestions; profiles for another stack are flagged
	 *   (scaffolding rejects a profile whose stack differs from the chosen stack).
	 * - `showPreview` (default true): collapsible rendered-prompt preview.
	 * - `label` (default 'Prompt profile'; '' hides it), `idPrefix`, `disabled`.
	 * - `onchange(profile | null)` after the user picks.
	 */
	import { onMount } from 'svelte';
	import {
		getGenerationProfiles,
		getProfilePreview,
		suggestProfiles,
		type GenerationProfile,
		type ProfilePreview,
		type ProfileSuggestion,
	} from '$lib/api/generation';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { cn } from '$lib/utils';
	import { Label } from '$lib/components/ui/label';
	import * as Select from '$lib/components/ui/select';
	import * as Tabs from '$lib/components/ui/tabs';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import { summarizeLLMParams } from './llm-params';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';

	interface Props {
		selectedId?: number | null;
		profiles?: GenerationProfile[];
		appIds?: number[];
		stackSlug?: string;
		showPreview?: boolean;
		label?: string;
		idPrefix?: string;
		disabled?: boolean;
		onchange?: (profile: GenerationProfile | null) => void;
	}

	let {
		selectedId = $bindable(null),
		profiles: providedProfiles,
		appIds = [],
		stackSlug = '',
		showPreview = true,
		label = 'Prompt profile',
		idPrefix = 'profile-picker',
		disabled = false,
		onchange,
	}: Props = $props();

	const AUTO = 'auto';
	const PROVENANCE: Record<ProfileSuggestion['provenance'], string> = {
		'app-pilot': 'app pilot',
		'stack-default': 'stack default',
		'system-default': 'system default',
		catalog: 'catalog fallback',
	};

	let fetched = $state<GenerationProfile[]>([]);
	let loading = $state(false);
	let error = $state('');
	let suggestions = $state<ProfileSuggestion[]>([]);
	let suggesting = $state(false);
	let previewOpen = $state(false);
	let previewApp = $state('');
	let previewStage = $state('backend');
	let preview = $state<ProfilePreview | null>(null);
	let previewLoading = $state(false);
	let previewError = $state('');

	const profiles = $derived(providedProfiles ?? fetched);
	const selected = $derived(selectedId != null ? profiles.find((p) => p.id === selectedId) : undefined);
	const appKey = $derived([...appIds].sort((a, b) => a - b).join(','));
	const suggestedIds = $derived(new Set(suggestions.map((s) => s.profile_id).filter((id) => id != null)));
	const triggerId = $derived(`${idPrefix}-select`);

	const autoSummary = $derived.by(() => {
		if (suggestions.length === 0) return '';
		const counts = new Map<string, number>();
		for (const s of suggestions) {
			const key = s.slug ? `${s.slug} (${PROVENANCE[s.provenance]})` : PROVENANCE[s.provenance];
			counts.set(key, (counts.get(key) ?? 0) + 1);
		}
		return [...counts.entries()]
			.map(([key, n]) => (suggestions.length > 1 ? `${key} ×${n}` : key))
			.join(', ');
	});

	const stackMismatch = $derived(
		!!selected && !!stackSlug && selected.scaffolding_slug !== stackSlug
	);

	// Apps the preview can render for: the suggestions carry their slugs.
	const previewApps = $derived(suggestions.map((s) => ({ value: s.app_slug, label: s.app_slug })));
	const previewAppSlug = $derived(
		previewApps.some((a) => a.value === previewApp) ? previewApp : (previewApps[0]?.value ?? '')
	);
	const previewProfileSlug = $derived(
		selected?.slug ?? suggestions.find((s) => s.app_slug === previewAppSlug)?.slug ?? ''
	);

	async function load() {
		loading = true;
		error = '';
		try {
			fetched = await getGenerationProfiles();
		} catch (e) {
			error = formatApiError(e, 'Could not load profiles.');
		} finally {
			loading = false;
		}
	}

	onMount(() => {
		if (!providedProfiles) void load();
	});

	// What Auto resolves to for the selected apps (debounced, stale requests aborted).
	$effect(() => {
		const key = appKey;
		const stack = stackSlug;
		if (!key) {
			suggestions = [];
			suggesting = false;
			return;
		}
		const controller = new AbortController();
		suggesting = true;
		const timer = setTimeout(() => {
			suggestProfiles(key.split(',').map(Number), stack || undefined, { signal: controller.signal })
				.then((result) => {
					suggestions = result;
				})
				.catch((e: unknown) => {
					if (!isAbortError(e)) suggestions = [];
				})
				.finally(() => {
					if (!controller.signal.aborted) suggesting = false;
				});
		}, 300);
		return () => {
			clearTimeout(timer);
			controller.abort();
		};
	});

	// Rendered prompts, only while the preview is open.
	$effect(() => {
		if (!showPreview || !previewOpen || !previewProfileSlug) {
			preview = null;
			previewError = '';
			return;
		}
		const slug = previewProfileSlug;
		const app = previewAppSlug || undefined;
		const controller = new AbortController();
		previewLoading = true;
		previewError = '';
		getProfilePreview(slug, app, { signal: controller.signal })
			.then((result) => {
				preview = result;
			})
			.catch((e: unknown) => {
				if (!isAbortError(e)) {
					preview = null;
					previewError = formatApiError(e, 'Preview failed.');
				}
			})
			.finally(() => {
				if (!controller.signal.aborted) previewLoading = false;
			});
		return () => controller.abort();
	});

	function pick(value: string) {
		selectedId = value === AUTO || value === '' ? null : Number(value);
		onchange?.(selectedId != null ? (profiles.find((p) => p.id === selectedId) ?? null) : null);
	}

	const previewStages = $derived(
		preview?.rendered
			? Object.entries(preview.rendered).map(([stage, messages]) => ({
					stage,
					system: messages.system,
					user: messages.user,
				}))
			: Object.entries(preview?.prompt_templates ?? {}).map(([stage, messages]) => ({
					stage,
					system: messages.system ?? '',
					user: messages.user ?? '',
				}))
	);
	const activeStage = $derived(
		previewStages.some((s) => s.stage === previewStage) ? previewStage : (previewStages[0]?.stage ?? '')
	);

	const chip = 'rounded-full border px-1.5 text-[11px] font-medium leading-4';
</script>

{#snippet profileLine(profile: GenerationProfile)}
	<span class="flex min-w-0 flex-col">
		<span class="flex min-w-0 flex-wrap items-center gap-1.5">
			<span class="truncate font-medium">{profile.name}</span>
			<span class="font-mono text-[11px] text-muted-foreground">v{profile.version}</span>
			{#if profile.is_default}
				<span class={cn(chip, 'border-border bg-muted text-muted-foreground')}>default</span>
			{/if}
			{#if suggestedIds.has(profile.id)}
				<span class={cn(chip, 'border-transparent bg-primary/15 text-primary')}>suggested</span>
			{/if}
			{#if stackSlug && profile.scaffolding_slug !== stackSlug}
				<span class={cn(chip, 'border-warning/30 bg-warning/10 text-warning-fg')}>other stack</span>
			{/if}
		</span>
		<span class="truncate text-[11px] text-muted-foreground">
			{profile.scaffolding_slug} · {profile.block_refs?.length ?? 0} blocks{#if summarizeLLMParams(profile.llm_config)}
				· {summarizeLLMParams(profile.llm_config)}{/if}
		</span>
	</span>
{/snippet}

<div class="min-w-0 space-y-1.5" data-slot="profile-picker">
	{#if label}<Label for={triggerId}>{label}</Label>{/if}
	{#if error}
		<ErrorAlert message={error} onretry={load} />
	{:else}
		<Select.Root
			type="single"
			value={selectedId != null ? String(selectedId) : AUTO}
			onValueChange={pick}
			disabled={disabled || loading}
			items={[
				{ value: AUTO, label: 'Auto' },
				...profiles.map((p) => ({ value: String(p.id), label: p.name })),
			]}
		>
			<Select.Trigger id={triggerId} class="min-h-9 py-1.5 data-[size=default]:h-auto" aria-busy={loading}>
				{#if loading}
					<span class="flex items-center gap-2 text-muted-foreground">
						<LoaderCircle class="size-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />Loading profiles…
					</span>
				{:else if selected}
					{@render profileLine(selected)}
				{:else}
					<span class="flex min-w-0 flex-col">
						<span class="font-medium">Auto</span>
						<span class="truncate text-[11px] text-muted-foreground">
							{#if suggesting}resolving…{:else}{autoSummary || 'per app: app pilot, else the stack default'}{/if}
						</span>
					</span>
				{/if}
			</Select.Trigger>
			<Select.Content class="max-w-[min(32rem,calc(100vw-2rem))]">
				<Select.Item value={AUTO} label="Auto">
					<span class="flex min-w-0 flex-col">
						<span class="font-medium">Auto</span>
						<span class="truncate text-[11px] text-muted-foreground">
							{autoSummary || 'Resolved per app: app pilot, else the stack default'}
						</span>
					</span>
				</Select.Item>
				{#if profiles.length > 0}<Select.Separator />{/if}
				{#each profiles as profile (profile.id)}
					<Select.Item value={String(profile.id)} label={profile.name}>
						{@render profileLine(profile)}
					</Select.Item>
				{/each}
			</Select.Content>
		</Select.Root>

		{#if stackMismatch && selected}
			<p class="flex items-start gap-1.5 text-[11px] text-warning-fg" role="status">
				<TriangleAlert class="mt-px size-3.5 shrink-0" aria-hidden="true" />
				This profile targets {selected.scaffolding_slug}, but the selected stack is {stackSlug}. Pick a matching
				profile or stack — mismatched runs are rejected.
			</p>
		{:else if selected?.description}
			<p class="text-[11px] text-muted-foreground">{selected.description}</p>
		{/if}

		{#if showPreview}
			<div class="pt-1">
				<button
					type="button"
					class="flex cursor-pointer items-center gap-1 rounded text-xs font-medium text-muted-foreground transition-colors duration-150 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
					aria-expanded={previewOpen}
					aria-controls="{idPrefix}-preview"
					onclick={() => (previewOpen = !previewOpen)}
				>
					<ChevronRight
						class={cn('size-3.5 transition-transform duration-150', previewOpen && 'rotate-90')}
						aria-hidden="true"
					/>
					Preview prompts
				</button>
				{#if previewOpen}
					<div id="{idPrefix}-preview" class="mt-2 space-y-2 rounded-md border border-border p-3">
						{#if !previewProfileSlug}
							<p class="text-xs text-muted-foreground">
								{appIds.length === 0
									? 'Select an app spec (or a profile) to preview the prompts.'
									: 'Resolving which profile Auto uses…'}
							</p>
						{:else}
							<div class="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
								<span>
									Profile <span class="font-mono text-foreground">{previewProfileSlug}</span>
									{#if previewAppSlug}· app{/if}
								</span>
								{#if previewApps.length > 1}
									<Select.Simple
										size="sm"
										class="w-auto min-w-[10rem]"
										aria-label="App to render the preview for"
										value={previewAppSlug}
										items={previewApps}
										onValueChange={(v) => (previewApp = v)}
									/>
								{:else if previewAppSlug}
									<span class="font-mono text-foreground">{previewAppSlug}</span>
								{/if}
							</div>
							{#if previewLoading && !preview}
								<LoadingState label="rendering prompts" class="py-6" />
							{:else if previewError}
								<ErrorAlert message={previewError} />
							{:else if preview}
								{#if !preview.rendered}
									<p class="text-[11px] text-muted-foreground">
										Raw templates (no app selected, so nothing is rendered yet).
									</p>
								{/if}
								{#if summarizeLLMParams(preview.effective_llm)}
									<p class="text-[11px] text-muted-foreground">
										Effective LLM: <span class="font-mono">{summarizeLLMParams(preview.effective_llm)}</span>
									</p>
								{/if}
								{#if previewStages.length === 0}
									<p class="text-xs text-muted-foreground">This profile has no prompt blocks.</p>
								{:else}
									<Tabs.Root value={activeStage} onValueChange={(v) => (previewStage = v)}>
										<Tabs.List>
											{#each previewStages as entry (entry.stage)}
												<Tabs.Trigger value={entry.stage} class="capitalize">{entry.stage}</Tabs.Trigger>
											{/each}
										</Tabs.List>
										{#each previewStages as entry (entry.stage)}
											<Tabs.Content value={entry.stage} class="space-y-2">
												<p class="section-label">System</p>
												<pre class="terminal max-h-56 whitespace-pre-wrap break-words">{entry.system || '—'}</pre>
												<p class="section-label">User</p>
												<pre class="terminal max-h-72 whitespace-pre-wrap break-words">{entry.user || '—'}</pre>
											</Tabs.Content>
										{/each}
									</Tabs.Root>
								{/if}
							{/if}
						{/if}
					</div>
				{/if}
			</div>
		{/if}
	{/if}
</div>
