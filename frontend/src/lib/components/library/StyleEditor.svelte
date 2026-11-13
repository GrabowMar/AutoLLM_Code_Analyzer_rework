<script lang="ts">
	/**
	 * StyleEditor — `StyleContent`: styling system, component library, tone (slug + text), accessibility,
	 * the fragment template the model sees, adherence markers and the compatible frontends (plan
	 * resolution rejects incompatible stack × style pairs).
	 *
	 * Props: `value`, `onchange`, `readonly`.
	 */
	import * as Select from '$lib/components/ui/select';
	import { Input } from '$lib/components/ui/input';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Textarea } from '$lib/components/ui/textarea';
	import type { StyleContent } from '$lib/api/v3/types';
	import FormRow from './FormRow.svelte';
	import ListInput from './ListInput.svelte';
	import NumberInput from './NumberInput.svelte';
	import LibraryRefPicker from './LibraryRefPicker.svelte';

	interface Props {
		value: StyleContent;
		onchange: (v: StyleContent) => void;
		readonly?: boolean;
	}

	let { value, onchange, readonly = false }: Props = $props();

	const SYSTEMS = ['tailwind', 'plain-css', 'css-modules', 'component-library', 'model-choice'];
	const FRONTENDS = ['react', 'vue', 'svelte', 'none', 'free'];
	const set = (patch: Partial<StyleContent>) => onchange({ ...value, ...patch });
	const setLibrary = (patch: Partial<NonNullable<StyleContent['library']>>) =>
		set({ library: { name: '', packages: [], frameworks: [], ...(value.library ?? {}), ...patch } });
	const setAdherence = (patch: Partial<StyleContent['adherence']>) => set({ adherence: { ...value.adherence, ...patch } });
</script>

<div class="space-y-5">
	<div class="grid gap-4 md:grid-cols-3">
		<FormRow label="System">
			<Select.Simple value={value.system} items={SYSTEMS.map((s) => ({ value: s, label: s }))} disabled={readonly} onValueChange={(v) => set({ system: v as StyleContent['system'] })} />
		</FormRow>
		<FormRow label="Tone" for="style-tone" help="Tone preset slug (minimal, playful, corporate, …) or empty.">
			<Input id="style-tone" value={value.tone ?? ''} oninput={(e) => set({ tone: e.currentTarget.value.trim() || null })} class="font-mono" readonly={readonly} />
		</FormRow>
		<FormRow label="Accessibility">
			<Select.Simple value={value.accessibility} items={[{ value: 'unspecified', label: 'unspecified' }, { value: 'wcag-aa', label: 'WCAG AA' }]} disabled={readonly} onValueChange={(v) => set({ accessibility: v as StyleContent['accessibility'] })} />
		</FormRow>
	</div>
	<FormRow label="Tone text" help="The tone sentence the fragment may embed (style.tone_text).">
		<Textarea value={value.tone_text ?? ''} rows={2} oninput={(e: Event) => set({ tone_text: (e.currentTarget as HTMLTextAreaElement).value || null })} readonly={readonly} />
	</FormRow>

	<section class="space-y-3 rounded-md border border-border p-3" aria-labelledby="style-library">
		<div class="flex items-center justify-between gap-2">
			<h3 id="style-library" class="section-label">Component library</h3>
			<label class="flex items-center gap-2 text-xs">
				<Checkbox checked={value.library !== null} disabled={readonly} onCheckedChange={(c) => (c ? setLibrary({}) : set({ library: null }))} /> Uses a library
			</label>
		</div>
		{#if value.library}
			<div class="grid gap-4 md:grid-cols-3">
				<FormRow label="Name"><Input value={value.library.name} oninput={(e) => setLibrary({ name: e.currentTarget.value })} class="h-8" readonly={readonly} /></FormRow>
				<FormRow label="Packages"><ListInput value={value.library.packages} {readonly} onchange={(v) => setLibrary({ packages: v })} /></FormRow>
				<FormRow label="Frameworks"><ListInput value={value.library.frameworks} {readonly} onchange={(v) => setLibrary({ frameworks: v })} /></FormRow>
			</div>
		{/if}
	</section>

	<FormRow label="Compatible frontends" inline help="Stacks whose frontend is not listed cannot be crossed with this style.">
		<div class="flex flex-wrap gap-4">
			{#each FRONTENDS as fe (fe)}
				<label class="flex items-center gap-2 text-sm">
					<Checkbox
						checked={value.compatible_frontends.includes(fe)}
						disabled={readonly}
						onCheckedChange={(c) => set({ compatible_frontends: c ? [...new Set([...value.compatible_frontends, fe])] : value.compatible_frontends.filter((f) => f !== fe) })}
					/>
					{fe}
				</label>
			{/each}
		</div>
	</FormRow>

	<FormRow label="Fragment" inline help="The prompt template (role fragment) the brief embeds as style.fragment_text.">
		<LibraryRefPicker kind="prompt" value={[value.fragment]} filter={(i) => (i.content as { role?: string }).role === 'fragment'} disabled={readonly} onchange={(r) => r[0] && set({ fragment: r[0] })} />
	</FormRow>

	<section class="space-y-3" aria-labelledby="style-adherence">
		<h3 id="style-adherence" class="section-label">Adherence</h3>
		<div class="grid gap-4 md:grid-cols-3">
			<FormRow label="Required markers"><ListInput value={value.adherence.required_markers} {readonly} onchange={(v) => setAdherence({ required_markers: v })} /></FormRow>
			<FormRow label="Forbidden markers"><ListInput value={value.adherence.forbidden_markers} {readonly} onchange={(v) => setAdherence({ forbidden_markers: v })} /></FormRow>
			<FormRow label="Min class share" help="Tailwind: share of elements with utility classes (0–1).">
				<NumberInput value={value.adherence.class_share_min} min={0} max={1} step={0.05} {readonly} placeholder="unset" onchange={(n) => setAdherence({ class_share_min: n })} />
			</FormRow>
		</div>
	</section>
</div>
