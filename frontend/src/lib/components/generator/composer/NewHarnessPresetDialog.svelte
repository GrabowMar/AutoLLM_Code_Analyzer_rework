<script lang="ts">
	/**
	 * NewHarnessPresetDialog — create a harness preset from the composer
	 * (schema-driven options; refuses an all-default configuration, which the
	 * harness's system preset already covers).
	 *
	 * Props:
	 * - `open` (bindable), `harnesses` (from `GET /agent/config/`; only those with
	 *   an option schema are offered), `harness` (bindable: preselected harness name).
	 * - `onsaved(preset)`: after a successful create; the caller selects it.
	 */
	import { untrack } from 'svelte';
	import { toast } from 'svelte-sonner';
	import {
		createRunPreset,
		validateRunPreset,
		type AgentHarnessInfo,
		type HarnessOptions,
		type RunPreset,
	} from '$lib/api/generation';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import * as Dialog from '$lib/components/ui/dialog';
	import * as Select from '$lib/components/ui/select';
	import { Button } from '$lib/components/ui/button';
	import { Label } from '$lib/components/ui/label';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import HarnessOptionsEditor from '../HarnessOptionsEditor.svelte';
	import { invalidatePresets } from '../presets.svelte';
	import PresetMetaFields, { presetSlugError } from './PresetMetaFields.svelte';
	import { stableKey } from './draft';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';

	interface Props {
		open: boolean;
		harnesses: AgentHarnessInfo[];
		harness?: string;
		onsaved: (preset: RunPreset<'harness'>) => void;
	}

	let { open = $bindable(false), harnesses, harness = $bindable(''), onsaved }: Props = $props();

	let options = $state<HarnessOptions>({});
	let optionsInvalid = $state(false);
	let name = $state('');
	let slug = $state('');
	let description = $state('');
	let saving = $state(false);
	let error = $state('');
	let slugError = $state('');
	let serverErrors = $state<string[]>([]);
	let validating = $state(false);

	const tunable = $derived(harnesses.filter((h) => h.option_schema.length > 0));
	const info = $derived(tunable.find((h) => h.name === harness));
	const allDefault = $derived(Object.keys(options).length === 0);
	const optionsKey = $derived(stableKey(options));
	const canSave = $derived(
		!!info &&
			!saving &&
			!validating &&
			!allDefault &&
			!optionsInvalid &&
			serverErrors.length === 0 &&
			!!name.trim() &&
			!presetSlugError(slug)
	);

	$effect(() => {
		if (!open) return;
		untrack(() => {
			if (!tunable.some((h) => h.name === harness)) harness = tunable[0]?.name ?? '';
			options = {};
			name = '';
			slug = '';
			description = '';
			error = '';
			slugError = '';
			serverErrors = [];
		});
	});

	// Live server validation (debounced; the client already drops default values).
	$effect(() => {
		if (!open || !info || allDefault) {
			serverErrors = [];
			validating = false;
			return;
		}
		const config = { harness: info.name, options: JSON.parse(optionsKey) as HarnessOptions };
		const controller = new AbortController();
		validating = true;
		const timer = setTimeout(() => {
			validateRunPreset('harness', config, { signal: controller.signal })
				.then((result) => {
					serverErrors = result.ok ? [] : result.errors;
				})
				.catch((e: unknown) => {
					if (!isAbortError(e)) serverErrors = [formatApiError(e, 'Validation failed.')];
				})
				.finally(() => {
					if (!controller.signal.aborted) validating = false;
				});
		}, 300);
		return () => {
			clearTimeout(timer);
			controller.abort();
		};
	});

	function pickHarness(next: string) {
		harness = next;
		options = {};
	}

	async function save() {
		if (!canSave || !info) return;
		saving = true;
		error = '';
		slugError = '';
		try {
			const preset = await createRunPreset({
				kind: 'harness',
				slug,
				name: name.trim(),
				description: description.trim(),
				config: { harness: info.name, options },
			});
			invalidatePresets('harness');
			toast.success(`Saved harness preset “${preset.name}”`);
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
	<Dialog.Content class="max-h-[90dvh] w-[calc(100%-2rem)] max-w-xl overflow-y-auto">
		<Dialog.Header>
			<Dialog.Title>New harness preset</Dialog.Title>
			<Dialog.Description class="text-sm text-muted-foreground">
				A named, versioned set of harness options. Every model in a batch runs the preset identically.
			</Dialog.Description>
		</Dialog.Header>

		<div class="mt-4 space-y-4">
			{#if tunable.length === 0}
				<p class="text-sm text-muted-foreground">No harness has tunable options.</p>
			{:else}
				<div class="space-y-1.5">
					<Label class="text-xs" for="new-harness-preset-harness">Harness</Label>
					<Select.Simple
						id="new-harness-preset-harness"
						size="sm"
						class="sm:max-w-xs"
						value={harness}
						items={tunable.map((h) => ({ value: h.name, label: `${h.label} ${h.version}`, description: h.description }))}
						disabled={saving}
						onValueChange={pickHarness}
					/>
				</div>
				{#if info}
					{#key info.name}
						<HarnessOptionsEditor
							schema={info.option_schema}
							bind:options
							bind:invalid={optionsInvalid}
							defaults={info.default_options}
							idPrefix="new-harness-preset-opt"
							disabled={saving}
						/>
					{/key}
					{#if allDefault}
						<p class="text-[11px] text-muted-foreground" role="status">
							Change at least one option — the defaults are already available as the system preset
							<span class="font-mono">{info.name}-default</span>.
						</p>
					{/if}
				{/if}
				{#if serverErrors.length > 0}
					<ErrorAlert title="These options can’t be saved" message={serverErrors.join(' ')} />
				{/if}
				<PresetMetaFields bind:name bind:slug bind:description idPrefix="new-harness-preset" {slugError} disabled={saving} />
				{#if error}<ErrorAlert message={error} />{/if}
			{/if}
		</div>

		<Dialog.Footer class="mt-6">
			<Button variant="outline" size="sm" disabled={saving} onclick={() => (open = false)}>Cancel</Button>
			<Button size="sm" disabled={!canSave} onclick={save}>
				{#if saving || validating}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{/if}
				Create preset
			</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>
