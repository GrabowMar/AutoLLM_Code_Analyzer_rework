<script lang="ts">
	/**
	 * SavePresetDialog — "Save as preset…" for the composer's current sampling
	 * or budget settings (the selected preset with the inline overrides on top).
	 *
	 * Props:
	 * - `open` (bindable), `kind` ('sampling' | 'budget').
	 * - `config`: the config to store (validated live via `/presets/validate/`).
	 * - `basedOn`: the preset the overrides were applied to (for the copy).
	 * - `onsaved(preset)`: after a successful create; the caller selects it.
	 */
	import { untrack } from 'svelte';
	import { toast } from 'svelte-sonner';
	import {
		createRunPreset,
		validateRunPreset,
		type PresetConfigByKind,
		type RunPreset,
	} from '$lib/api/generation';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import * as Dialog from '$lib/components/ui/dialog';
	import { Button } from '$lib/components/ui/button';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import { PRESET_KIND_LABELS, invalidatePresets, summarizePresetConfig } from '../presets.svelte';
	import PresetMetaFields, { presetSlugError } from './PresetMetaFields.svelte';
	import { stableKey } from './draft';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';

	type SavableKind = 'sampling' | 'budget';

	interface Props {
		open: boolean;
		kind: SavableKind;
		config: PresetConfigByKind[SavableKind];
		basedOn?: RunPreset | null;
		onsaved: (preset: RunPreset) => void;
	}

	let { open = $bindable(false), kind, config, basedOn = null, onsaved }: Props = $props();

	let name = $state('');
	let slug = $state('');
	let description = $state('');
	let saving = $state(false);
	let error = $state('');
	let slugError = $state('');
	let validation = $state<{ ok: boolean; errors: string[]; config: unknown } | null>(null);
	let validating = $state(false);

	const configKey = $derived(stableKey(config));
	const summary = $derived(
		summarizePresetConfig(kind, (validation?.config as PresetConfigByKind[SavableKind] | null) ?? config)
	);
	const canSave = $derived(
		!saving && !validating && !!name.trim() && !presetSlugError(slug) && validation?.ok === true
	);

	// Fresh form each time the dialog opens.
	$effect(() => {
		if (!open) return;
		untrack(() => {
			name = '';
			slug = '';
			description = '';
			error = '';
			slugError = '';
		});
	});

	$effect(() => {
		if (!open) return;
		const body = JSON.parse(configKey) as PresetConfigByKind[SavableKind];
		const controller = new AbortController();
		validating = true;
		validation = null;
		validateRunPreset(kind, body, { signal: controller.signal })
			.then((result) => {
				validation = result;
			})
			.catch((e: unknown) => {
				if (!isAbortError(e)) validation = { ok: false, errors: [formatApiError(e, 'Validation failed.')], config: null };
			})
			.finally(() => {
				if (!controller.signal.aborted) validating = false;
			});
		return () => controller.abort();
	});

	async function save() {
		if (!canSave) return;
		saving = true;
		error = '';
		slugError = '';
		try {
			const preset = await createRunPreset({
				kind,
				slug,
				name: name.trim(),
				description: description.trim(),
				config: (validation?.config as PresetConfigByKind[SavableKind] | null) ?? config,
			});
			invalidatePresets(kind);
			toast.success(`Saved ${PRESET_KIND_LABELS[kind].toLowerCase()} “${preset.name}”`);
			open = false;
			onsaved(preset);
		} catch (e) {
			const message = formatApiError(e, 'Could not save the preset.');
			if (/slug/i.test(message)) slugError = message;
			else error = message;
		} finally {
			saving = false;
		}
	}
</script>

<Dialog.Root bind:open>
	<Dialog.Content class="max-h-[90dvh] w-[calc(100%-2rem)] max-w-lg overflow-y-auto">
		<Dialog.Header>
			<Dialog.Title>Save as {PRESET_KIND_LABELS[kind].toLowerCase()}</Dialog.Title>
			<Dialog.Description class="text-sm text-muted-foreground">
				{#if basedOn}
					{basedOn.name} v{basedOn.version} with your overrides, stored as a new versioned preset you can reuse in
					other batches and experiments.
				{:else}
					Your current settings, stored as a new versioned preset you can reuse in other batches and experiments.
				{/if}
			</Dialog.Description>
		</Dialog.Header>

		<div class="mt-4 space-y-4">
			<div class="rounded-md border border-border bg-surface-2 px-3 py-2 text-xs">
				<p class="section-label">Stored settings</p>
				<p class="mt-1 font-mono text-foreground">{summary}</p>
				{#if kind === 'sampling'}
					<p class="mt-1 text-muted-foreground">
						Provider routing, stop sequences and response format are not sampling settings and stay as overrides.
					</p>
				{/if}
			</div>
			{#if validating}
				<p class="flex items-center gap-2 text-xs text-muted-foreground" role="status">
					<LoaderCircle class="size-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />Checking the settings…
				</p>
			{:else if validation && !validation.ok}
				<ErrorAlert title="These settings can’t be saved as a preset" message={validation.errors.join(' ')} />
			{/if}

			<PresetMetaFields bind:name bind:slug bind:description idPrefix="save-{kind}-preset" {slugError} disabled={saving} />
			{#if error}<ErrorAlert message={error} />{/if}
		</div>

		<Dialog.Footer class="mt-6">
			<Button variant="outline" size="sm" disabled={saving} onclick={() => (open = false)}>Cancel</Button>
			<Button size="sm" disabled={!canSave} onclick={save}>
				{#if saving}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{/if}
				Save preset
			</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>
