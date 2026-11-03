<script lang="ts">
	/**
	 * DetectorEditor — `DetectorContent`: engine (Semgrep, tree-sitter query, regex, probe, manual), the
	 * languages, the rule text, the pinned tool version, the feature it marks and its polarity, the field
	 * holding the rule's own severity label (copied verbatim, never computed), CWEs, scopes and taint.
	 *
	 * Props: `value`, `onchange`, `readonly`, `prefill` (WP-16's "Create detector from selection": merged
	 * over `value` once, on mount, and emitted so the caller's draft holds it).
	 */
	import { onMount } from 'svelte';
	import * as Select from '$lib/components/ui/select';
	import { Input } from '$lib/components/ui/input';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Segmented } from '$lib/components/ui/segmented';
	import type { DetectorContent } from '$lib/api/v3/types';
	import { DETECTOR_ENGINES } from '$lib/api/v3/types';
	import FormRow from './FormRow.svelte';
	import ListInput from './ListInput.svelte';

	interface Props {
		value: DetectorContent;
		onchange: (v: DetectorContent) => void;
		readonly?: boolean;
		prefill?: Partial<DetectorContent> | null;
	}

	let { value, onchange, readonly = false, prefill = null }: Props = $props();

	onMount(() => {
		if (prefill && Object.keys(prefill).length) onchange({ ...value, ...prefill });
	});

	const set = (patch: Partial<DetectorContent>) => onchange({ ...value, ...patch });
	const ruleHelp: Record<DetectorContent['engine'], string> = {
		semgrep: 'Semgrep rule YAML (one rule). The analyzer workspace’s Semgrep version runs it; errors[] mark the result error, never absent.',
		ts_query: 'A tree-sitter query; a match marks the feature present.',
		regex: 'A Python regular expression over the normalized source.',
		probe: 'Probe detectors read probe outcomes; no rule text.',
		manual: 'Manual detectors are filled by cluster labels; no rule text.',
	};
	const FEATURE_RE = /^[a-z0-9][a-z0-9_-]*$/;
</script>

<div class="space-y-5">
	<div class="grid gap-4 md:grid-cols-3">
		<FormRow label="Engine">
			<Select.Simple value={value.engine} items={DETECTOR_ENGINES.map((e) => ({ value: e, label: e }))} disabled={readonly} onValueChange={(v) => set({ engine: v as DetectorContent['engine'] })} />
		</FormRow>
		<FormRow label="Feature key" for="det-feature" help="Lower-case slug the prevalence tables use." error={value.feature_key && !FEATURE_RE.test(value.feature_key) ? 'Use a-z, 0-9, _ and -.' : null}>
			<Input id="det-feature" value={value.feature_key} oninput={(e) => set({ feature_key: e.currentTarget.value.trim() })} class="font-mono" readonly={readonly} />
		</FormRow>
		<FormRow label="Polarity">
			<Segmented aria-label="Polarity" value={value.polarity} disabled={readonly} items={[{ value: 'insecure', label: 'Insecure' }, { value: 'secure', label: 'Secure' }, { value: 'neutral', label: 'Neutral' }]} onValueChange={(v) => set({ polarity: v as DetectorContent['polarity'] })} />
		</FormRow>
	</div>
	<FormRow label="Description" for="det-desc">
		<Input id="det-desc" value={value.description} oninput={(e) => set({ description: e.currentTarget.value })} readonly={readonly} />
	</FormRow>
	<div class="grid gap-4 md:grid-cols-3">
		<FormRow label="Languages"><ListInput value={value.languages} {readonly} placeholder="python, typescript" onchange={(v) => set({ languages: v })} /></FormRow>
		<FormRow label="CWEs"><ListInput value={value.cwe} {readonly} placeholder="cwe-89" onchange={(v) => set({ cwe: v })} /></FormRow>
		<FormRow label="Tool severity field" help="The rule's own severity label, copied verbatim (e.g. severity). Empty when the tool rates none.">
			<Input value={value.tool_severity_field ?? ''} oninput={(e) => set({ tool_severity_field: e.currentTarget.value.trim() || null })} class="h-8 font-mono" readonly={readonly} />
		</FormRow>
		<FormRow label="Tool name"><Input value={value.tool.name} oninput={(e) => set({ tool: { ...value.tool, name: e.currentTarget.value } })} class="h-8 font-mono" readonly={readonly} /></FormRow>
		<FormRow label="Tool version" help="Recorded with every result."><Input value={value.tool.version} oninput={(e) => set({ tool: { ...value.tool, version: e.currentTarget.value } })} class="h-8 font-mono" readonly={readonly} /></FormRow>
		<FormRow label="Scopes">
			<div class="flex gap-4 pt-1">
				{#each ['span', 'file'] as const as scope (scope)}
					<label class="flex items-center gap-1.5 text-sm">
						<Checkbox checked={value.scopes.includes(scope)} disabled={readonly || value.taint} onCheckedChange={(c) => set({ scopes: c ? [...new Set([...value.scopes, scope])] : value.scopes.filter((s) => s !== scope) })} />
						{scope}
					</label>
				{/each}
				<label class="flex items-center gap-1.5 text-sm">
					<Checkbox checked={value.taint} disabled={readonly} onCheckedChange={(c) => set({ taint: c === true, scopes: c ? ['file'] : value.scopes })} />
					taint
				</label>
			</div>
		</FormRow>
	</div>
	{#if value.engine !== 'probe' && value.engine !== 'manual'}
		<FormRow label="Rule" help={ruleHelp[value.engine]}>
			<textarea
				value={value.rule}
				oninput={(e) => set({ rule: e.currentTarget.value })}
				readonly={readonly}
				rows={14}
				spellcheck="false"
				aria-label="Rule"
				class="block w-full rounded-md border border-input bg-surface-1 px-3 py-2 font-mono text-xs read-only:bg-muted/30"
			></textarea>
		</FormRow>
	{:else}
		<p class="text-xs text-muted-foreground">{ruleHelp[value.engine]}</p>
	{/if}
</div>
