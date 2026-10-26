<script lang="ts" module>
	import type { ProfileConfig } from '$lib/api/analysis';

	export type ProfileEditorSection = 'tools' | 'reviewer' | 'scope' | 'outcomes';

	export interface ProfileValidationState {
		/** idle: not validated (readonly / disabled) · pending · valid · invalid · error (request failed). */
		status: 'idle' | 'pending' | 'valid' | 'invalid' | 'error';
		/** Config-relative field errors of the last validation. */
		errors: Record<string, string>;
		/** Content hash of the normalized config (equal hash = identical analysis config). */
		contentHash: string | null;
		/** The normalized config the server would store. */
		config: ProfileConfig | null;
		/** Request failure message (status `error`). */
		message: string;
	}
</script>

<script lang="ts">
	/**
	 * ProfileEditor — edit an analysis profile config: tools (by category,
	 * enable / required, options drawer), AI reviewer, scope and outcomes,
	 * validated live through `POST /profiles/validate/`.
	 *
	 *   <ProfileEditor {config} onchange={(c) => (config = c)} errors={fieldErrors}
	 *                  bind:validation bind:invalid />
	 *
	 * Props:
	 * - `config` (`ProfileConfig`): the full config (a profile's `config`,
	 *   or `/profiles/defaults/` `config` for a new one). Replaced, never mutated.
	 * - `onchange(config)`: the next config after each user edit.
	 * - `errors`: server field errors, e.g. a 422 save's `field_errors`
	 *   (`config.reviewer.samples`) or validate keys (`reviewer.samples`). They
	 *   show until the config changes; live validation errors are merged in.
	 * - `readonly`: render values only (system profiles, old versions, locked plans).
	 * - `defaults` (`ProfileDefaults`): catalog + reviewer options; loaded (and
	 *   cached per page) when omitted.
	 * - `validate` (default true): live validation while editable.
	 * - `validation` (bindable, output): {@link ProfileValidationState}.
	 * - `invalid` (bindable, output): true while a field holds input the editor
	 *   could not apply (incomplete number, bad JSON) — block saving on it, and
	 *   on `validation.status !== 'valid'`.
	 * - `section` (bindable): the open tab.
	 * - `idPrefix` (default 'profile'): prefix of element ids (two editors on a page need distinct prefixes).
	 */
	import { untrack } from 'svelte';
	import { isAbortError, formatApiError } from '$lib/api/core';
	import { validateProfileConfig, type ProfileDefaults } from '$lib/api/analysis';
	import { cn } from '$lib/utils';
	import { Button } from '$lib/components/ui/button';
	import * as Tabs from '$lib/components/ui/tabs';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ToolsSection from './ToolsSection.svelte';
	import ReviewerConfigEditor from './ReviewerConfigEditor.svelte';
	import ScopeEditor from './ScopeEditor.svelte';
	import OutcomesEditor from './OutcomesEditor.svelte';
	import { loadProfileDefaults, reviewerOptionsError, reviewerOptionsOf } from './defaults';
	import { provideInvalidFields } from './invalid-fields.svelte';
	import {
		countErrors,
		errorsUnder,
		normalizeConfigErrors,
		reindexToolErrors,
		shortHash,
		stableStringify
	} from './profile-utils';
	import Bot from '@lucide/svelte/icons/bot';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import Crosshair from '@lucide/svelte/icons/crosshair';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Ruler from '@lucide/svelte/icons/ruler';
	import ScanSearch from '@lucide/svelte/icons/scan-search';

	interface Props {
		config: ProfileConfig;
		onchange?: (config: ProfileConfig) => void;
		errors?: Record<string, string>;
		readonly?: boolean;
		defaults?: ProfileDefaults | null;
		validate?: boolean;
		validation?: ProfileValidationState;
		invalid?: boolean;
		section?: ProfileEditorSection;
		idPrefix?: string;
	}

	let {
		config,
		onchange,
		errors = {},
		readonly = false,
		defaults = undefined,
		validate = true,
		validation = $bindable({
			status: 'idle',
			errors: {},
			contentHash: null,
			config: null,
			message: ''
		}),
		invalid = $bindable(false),
		section = $bindable('tools'),
		idPrefix = 'profile'
	}: Props = $props();

	const VALIDATE_DEBOUNCE_MS = 400;
	const SECTIONS: ProfileEditorSection[] = ['tools', 'reviewer', 'scope', 'outcomes'];

	const tracker = provideInvalidFields();
	$effect(() => {
		invalid = tracker.any;
	});

	// ── catalog + reviewer options ─────────────────────────────────────
	let loaded = $state<ProfileDefaults | null>(null);
	let loadError = $state('');
	const catalog = $derived(defaults ?? loaded);
	const reviewerOptions = $derived(reviewerOptionsOf(catalog));
	const reviewerError = $derived(reviewerOptionsError(catalog));

	async function loadDefaults(force = false) {
		loadError = '';
		try {
			loaded = await loadProfileDefaults({ force });
		} catch (e) {
			loadError = formatApiError(e, 'Could not load the tool catalog.');
		}
	}

	$effect(() => {
		if (!defaults && !untrack(() => loaded)) void loadDefaults();
	});

	// ── errors: external (until the config changes) + live validation ──
	const configKey = $derived(stableStringify(config));
	const toolSlugs = $derived(config.tools.map((t) => t.slug));

	// External errors belong to the config the parent submitted (the current
	// one when they arrive); they hide once the config changes.
	let external = $state<{ key: string; basis: string; errors: Record<string, string> }>({
		key: '{}',
		basis: '',
		errors: {}
	});
	$effect(() => {
		const normalized = normalizeConfigErrors(errors);
		const key = stableStringify(normalized);
		untrack(() => {
			if (key !== external.key) external = { key, basis: configKey, errors: normalized };
		});
	});
	const externalActive = $derived(configKey === external.basis ? external.errors : {});

	// Live errors are kept while the next validation is pending (no flicker);
	// tool indices are remapped from the validated list to the current one.
	let liveTools = $state<string[]>([]);
	const liveErrors = $derived(
		validation.status === 'idle' || validation.status === 'error'
			? {}
			: reindexToolErrors(validation.errors, liveTools, toolSlugs)
	);
	const allErrors = $derived({ ...externalActive, ...liveErrors });

	const sectionErrors = $derived({
		tools: countErrors(allErrors, 'tools'),
		reviewer: countErrors(allErrors, 'reviewer'),
		scope: countErrors(allErrors, 'scope'),
		outcomes: countErrors(allErrors, 'outcomes')
	});
	const otherErrors = $derived(
		Object.entries(allErrors).filter(
			([key]) => !SECTIONS.some((s) => key === s || key.startsWith(`${s}.`))
		)
	);

	// ── live validation ────────────────────────────────────────────────
	let controller: AbortController | null = null;

	async function runValidation(snapshot: string) {
		controller?.abort();
		const request = new AbortController();
		controller = request;
		const submitted = JSON.parse(snapshot) as ProfileConfig;
		try {
			const result = await validateProfileConfig(submitted, { signal: request.signal });
			if (controller !== request) return;
			liveTools = submitted.tools.map((t) => t.slug);
			validation = {
				status: result.ok ? 'valid' : 'invalid',
				errors: result.errors ?? {},
				contentHash: result.content_hash,
				config: result.config,
				message: ''
			};
		} catch (e) {
			if (isAbortError(e) || controller !== request) return;
			validation = {
				status: 'error',
				errors: {},
				contentHash: null,
				config: null,
				message: formatApiError(e, 'Could not validate the profile.')
			};
		}
	}

	$effect(() => {
		const snapshot = configKey;
		if (readonly || !validate) {
			untrack(() => {
				controller?.abort();
				controller = null;
				if (validation.status !== 'idle') {
					validation = { status: 'idle', errors: {}, contentHash: null, config: null, message: '' };
				}
			});
			return;
		}
		untrack(() => {
			validation = { ...validation, status: 'pending' };
		});
		const timer = setTimeout(() => void runValidation(snapshot), VALIDATE_DEBOUNCE_MS);
		return () => clearTimeout(timer);
	});

	$effect(() => {
		return () => controller?.abort();
	});

	function retryValidation() {
		void runValidation(configKey);
	}

	// ── edits ──────────────────────────────────────────────────────────
	function emit(next: ProfileConfig) {
		if (readonly) return;
		onchange?.(next);
	}

	const id = (name: string) => `${idPrefix}-${name}`;
	const enabledTools = $derived(config.tools.length);
</script>

{#snippet errorCount(count: number)}
	{#if count > 0}
		<span
			class="inline-flex min-w-5 items-center justify-center rounded-full bg-destructive/15 px-1.5 text-[11px] font-semibold tabular-nums text-danger-fg"
			aria-label="{count} problem{count === 1 ? '' : 's'}">{count}</span
		>
	{/if}
{/snippet}

<div class="space-y-4" data-slot="profile-editor">
	{#if !readonly && validate}
		<div class="flex min-h-6 flex-wrap items-center gap-x-3 gap-y-1 text-xs" aria-live="polite">
			{#if validation.status === 'pending' || validation.status === 'idle'}
				<span class="inline-flex items-center gap-1.5 text-muted-foreground">
					<LoaderCircle
						class="size-3.5 animate-spin motion-reduce:animate-none"
						aria-hidden="true"
					/>Validating…
				</span>
			{:else if validation.status === 'valid'}
				<span class="inline-flex items-center gap-1.5 text-success-fg">
					<CircleCheck class="size-3.5" aria-hidden="true" />Valid configuration
				</span>
				<span
					class="font-mono text-[11px] text-muted-foreground"
					title="Content hash {validation.contentHash}"
				>
					#{shortHash(validation.contentHash)}
				</span>
			{:else if validation.status === 'invalid'}
				<span class="inline-flex items-center gap-1.5 font-medium text-danger-fg">
					<CircleAlert class="size-3.5" aria-hidden="true" />{Object.keys(validation.errors).length} problem{Object.keys(
						validation.errors
					).length === 1
						? ''
						: 's'} to fix
				</span>
			{:else if validation.status === 'error'}
				<span class="inline-flex items-center gap-1.5 text-warning-fg">
					<CircleAlert class="size-3.5" aria-hidden="true" />Couldn't validate: {validation.message}
				</span>
				<Button variant="ghost" size="sm" class="h-6 px-2" onclick={retryValidation}>Retry</Button>
			{/if}
			{#if invalid}
				<span class="inline-flex items-center gap-1.5 text-danger-fg">
					<CircleAlert class="size-3.5" aria-hidden="true" />{tracker.count} field{tracker.count ===
					1
						? ''
						: 's'} not applied
				</span>
			{/if}
		</div>
	{/if}

	{#if otherErrors.length > 0}
		<div
			class="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-danger-fg"
			role="alert"
		>
			<CircleAlert class="mt-0.5 size-4 shrink-0" aria-hidden="true" />
			<ul class="space-y-0.5">
				{#each otherErrors as [key, message] (key)}
					<li>{key ? `${key}: ` : ''}{message}</li>
				{/each}
			</ul>
		</div>
	{/if}

	{#if !catalog}
		{#if loadError}
			<ErrorAlert message={loadError} onretry={() => loadDefaults(true)} />
		{:else}
			<LoadingState label="loading tool catalog" class="py-10" />
		{/if}
	{:else}
		<Tabs.Root bind:value={section}>
			<Tabs.List aria-label="Profile sections">
				<Tabs.Trigger value="tools" id={id('tab-tools')}>
					<ScanSearch aria-hidden="true" />Tools
					<span class="text-[11px] tabular-nums text-muted-foreground">{enabledTools}</span>
					{@render errorCount(sectionErrors.tools)}
				</Tabs.Trigger>
				<Tabs.Trigger value="reviewer" id={id('tab-reviewer')}>
					<Bot aria-hidden="true" />AI reviewer
					<span
						class={cn(
							'rounded-full px-1.5 text-[11px] font-medium',
							config.reviewer.enabled
								? 'bg-primary/15 text-primary'
								: 'bg-muted text-muted-foreground'
						)}>{config.reviewer.enabled ? 'on' : 'off'}</span
					>
					{@render errorCount(sectionErrors.reviewer)}
				</Tabs.Trigger>
				<Tabs.Trigger value="scope" id={id('tab-scope')}>
					<Crosshair aria-hidden="true" />Scope
					{@render errorCount(sectionErrors.scope)}
				</Tabs.Trigger>
				<Tabs.Trigger value="outcomes" id={id('tab-outcomes')}>
					<Ruler aria-hidden="true" />Outcomes
					{@render errorCount(sectionErrors.outcomes)}
				</Tabs.Trigger>
			</Tabs.List>

			<Tabs.Content value="tools" class="pt-4">
				<ToolsSection
					tools={catalog.tools}
					{config}
					errors={allErrors}
					{readonly}
					idPrefix={id('tools')}
					onchange={emit}
				/>
			</Tabs.Content>
			<Tabs.Content value="reviewer" class="pt-4">
				{#if reviewerError}
					<ErrorAlert
						class="mb-4"
						title="Reviewer options are unavailable"
						message={reviewerError}
					/>
				{/if}
				<ReviewerConfigEditor
					config={config.reviewer}
					options={reviewerOptions}
					errors={errorsUnder(allErrors, 'reviewer')}
					{readonly}
					idPrefix={id('reviewer')}
					onchange={(reviewer) => emit({ ...config, reviewer })}
				/>
			</Tabs.Content>
			<Tabs.Content value="scope" class="pt-4">
				<ScopeEditor
					config={config.scope}
					errors={errorsUnder(allErrors, 'scope')}
					{readonly}
					idPrefix={id('scope')}
					onchange={(scope) => emit({ ...config, scope })}
				/>
			</Tabs.Content>
			<Tabs.Content value="outcomes" class="pt-4">
				<OutcomesEditor
					config={config.outcomes}
					errors={errorsUnder(allErrors, 'outcomes')}
					{readonly}
					idPrefix={id('outcomes')}
					onchange={(outcomes) => emit({ ...config, outcomes })}
				/>
			</Tabs.Content>
		</Tabs.Root>
	{/if}
</div>
