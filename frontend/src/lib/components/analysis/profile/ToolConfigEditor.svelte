<script lang="ts">
	/**
	 * ToolConfigEditor — schema-driven form for one analysis tool's options
	 * (`AnalysisTool.config_schema`).
	 *
	 * Props:
	 * - `tool` (`AnalysisTool`): schema, catalog defaults, name.
	 * - `config` (`ToolConfigValues`): the profile's effective config of this
	 *   tool (defaults merged). Replaced, never mutated.
	 * - `onchange(config)`: after each edit, with the full next config.
	 * - `errors`: server messages keyed by option name (`""` = the whole config).
	 * - `readonly`: show values without inputs' write access.
	 * - `idPrefix` (default `tool-config-<slug>`).
	 *
	 * Types map to controls: select → Select, multiselect → checkboxes,
	 * boolean → switch, integer/number → typed number input (binds numbers),
	 * string → text input with the schema pattern checked as you type.
	 */
	import type {
		AnalysisTool,
		JsonValue,
		ToolConfigField,
		ToolConfigValues
	} from '$lib/api/analysis';
	import { cn } from '$lib/utils';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Switch } from '$lib/components/ui/switch';
	import * as Select from '$lib/components/ui/select';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ChoiceChecklist from './ChoiceChecklist.svelte';
	import NumberField from './NumberField.svelte';
	import { cloneJson, describeOption, sameValue } from './profile-utils';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import SlidersHorizontal from '@lucide/svelte/icons/sliders-horizontal';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import X from '@lucide/svelte/icons/x';

	interface Props {
		tool: AnalysisTool;
		config: ToolConfigValues;
		onchange?: (config: ToolConfigValues) => void;
		errors?: Record<string, string>;
		readonly?: boolean;
		idPrefix?: string;
	}

	let { tool, config, onchange, errors = {}, readonly = false, idPrefix }: Props = $props();

	const prefix = $derived(idPrefix ?? `tool-config-${tool.slug}`);
	const schema = $derived(tool.config_schema);
	const known = $derived(new Set(schema.map((f) => f.name)));
	const unknownKeys = $derived(Object.keys(config).filter((key) => !known.has(key)));

	function defaultOf(field: ToolConfigField): JsonValue {
		return field.name in tool.default_config ? tool.default_config[field.name] : field.default;
	}

	function current(field: ToolConfigField): JsonValue {
		return field.name in config ? config[field.name] : defaultOf(field);
	}

	function isModified(field: ToolConfigField): boolean {
		return !sameValue(current(field), defaultOf(field));
	}

	const modifiedCount = $derived(schema.filter((f) => isModified(f)).length);

	function setValue(field: ToolConfigField, value: JsonValue) {
		onchange?.({ ...config, [field.name]: value });
	}

	function resetField(field: ToolConfigField) {
		setValue(field, cloneJson(defaultOf(field)));
	}

	function resetAll() {
		const next: ToolConfigValues = {};
		for (const field of schema) next[field.name] = cloneJson(defaultOf(field));
		onchange?.(next);
	}

	function removeUnknown(key: string) {
		const next = { ...config };
		delete next[key];
		onchange?.(next);
	}

	function patternProblem(field: ToolConfigField, value: JsonValue): string {
		if (typeof value !== 'string') return '';
		if (field.min_length != null && value.length < field.min_length)
			return `At least ${field.min_length} characters`;
		if (field.max_length != null && value.length > field.max_length)
			return `At most ${field.max_length} characters`;
		if (!field.pattern) return '';
		try {
			if (new RegExp(field.pattern).test(value)) return '';
		} catch {
			return '';
		}
		return field.placeholder ? `Expected a value like ${field.placeholder}` : 'Unexpected format';
	}

	function selectItems(field: ToolConfigField) {
		const fallback = defaultOf(field);
		return (field.options ?? []).map((o) => ({
			value: o.value,
			label: o.value === fallback ? `${o.label} (default)` : o.label
		}));
	}

	function rangeText(field: ToolConfigField): string {
		if (field.type === 'multiselect') {
			const parts: string[] = [];
			if (field.min_items != null) parts.push(`at least ${field.min_items}`);
			if (field.max_items != null) parts.push(`at most ${field.max_items}`);
			return parts.length ? ` · pick ${parts.join(', ')}` : '';
		}
		if (field.min == null && field.max == null) return '';
		return ` · range ${field.min ?? '…'}–${field.max ?? '…'}`;
	}
</script>

{#if schema.length === 0 && unknownKeys.length === 0}
	<EmptyState
		compact
		icon={SlidersHorizontal}
		title="No tunable options"
		description="{tool.name} runs with the configuration baked into the analyzer image, identically for every profile."
	/>
{:else}
	<div class="space-y-5" data-slot="tool-config-editor">
		<div class="flex flex-wrap items-center justify-between gap-2">
			<p class="text-xs text-muted-foreground" aria-live="polite">
				{modifiedCount === 0
					? 'All options at the catalog defaults.'
					: `${modifiedCount} option${modifiedCount === 1 ? ' differs' : 's differ'} from the catalog defaults.`}
			</p>
			{#if modifiedCount > 0 && !readonly}
				<Button variant="ghost" size="sm" class="h-7 px-2" onclick={resetAll}>
					<RotateCcw />Reset all
				</Button>
			{/if}
		</div>

		{#if errors['']}
			<p
				class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-danger-fg"
				role="alert"
			>
				{errors['']}
			</p>
		{/if}

		{#each schema as field (field.name)}
			{@const id = `${prefix}-${field.name}`}
			{@const value = current(field)}
			{@const modified = isModified(field)}
			{@const serverError = errors[field.name] ?? ''}
			<div class="space-y-1.5">
				<div class="flex flex-wrap items-center gap-x-2 gap-y-1">
					{#if field.type === 'multiselect'}
						<span id="{id}-label" class="text-sm font-medium">{field.label}</span>
					{:else}
						<Label for={id}>{field.label}</Label>
					{/if}
					<span class="font-mono text-[11px] text-muted-foreground">{field.name}</span>
					<span
						class="rounded border border-border px-1.5 text-[11px] text-muted-foreground"
						title={field.applies_to === 'argv'
							? 'Changes the command line the tool runs with'
							: 'Changes only how the tool output is read'}
					>
						{field.applies_to === 'argv' ? 'command line' : 'interpretation'}
					</span>
					{#if modified}
						<span class="rounded-full bg-primary/15 px-1.5 text-[11px] font-medium text-primary"
							>modified</span
						>
						{#if !readonly}
							<button
								type="button"
								class="cursor-pointer rounded text-[11px] text-muted-foreground underline-offset-2 transition-colors hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
								onclick={() => resetField(field)}
							>
								reset
							</button>
						{/if}
					{/if}
				</div>

				{#if field.type === 'select'}
					<Select.Simple
						{id}
						size="sm"
						class={cn('sm:max-w-xs', modified && 'border-primary/50')}
						value={String(value ?? '')}
						items={selectItems(field)}
						aria-describedby="{id}-help"
						aria-invalid={serverError ? true : undefined}
						disabled={readonly}
						onValueChange={(v) => setValue(field, v)}
					/>
				{:else if field.type === 'multiselect'}
					<ChoiceChecklist
						{id}
						labelledBy="{id}-label"
						items={(field.options ?? []).map((o) => ({ value: o.value, label: o.label }))}
						value={Array.isArray(value) ? value.map(String) : []}
						disabled={readonly}
						error={serverError}
						onchange={(values) => setValue(field, values)}
					/>
				{:else if field.type === 'boolean'}
					<Switch
						{id}
						checked={value === true}
						label={field.label}
						describedBy="{id}-help"
						size="sm"
						disabled={readonly}
						onCheckedChange={(checked) => setValue(field, checked)}
					/>
				{:else if field.type === 'integer' || field.type === 'number'}
					<NumberField
						{id}
						class="sm:max-w-xs"
						value={typeof value === 'number' ? value : null}
						integer={field.type === 'integer'}
						min={field.min}
						max={field.max}
						step={field.step}
						placeholder="default {describeOption(field, defaultOf(field))}"
						error={serverError}
						disabled={readonly}
						describedBy="{id}-help"
						onchange={(n) => {
							if (n !== null) setValue(field, n);
						}}
					/>
				{:else}
					{@const problem = patternProblem(field, value)}
					<Input
						{id}
						value={typeof value === 'string' ? value : String(value ?? '')}
						placeholder={field.placeholder ?? ''}
						autocomplete="off"
						spellcheck={false}
						class={cn(
							'h-8 font-mono text-xs sm:max-w-sm',
							modified && 'border-primary/50',
							(problem || serverError) && 'border-destructive'
						)}
						aria-invalid={problem || serverError ? true : undefined}
						aria-describedby="{id}-help"
						disabled={readonly}
						oninput={(e: Event) => setValue(field, (e.currentTarget as HTMLInputElement).value)}
					/>
					{#if serverError || problem}
						<p class="text-[11px] text-danger-fg">{serverError || problem}</p>
					{/if}
				{/if}

				{#if field.type === 'select' && serverError}
					<p class="text-[11px] text-danger-fg">{serverError}</p>
				{/if}
				<p id="{id}-help" class="text-[11px] leading-4 text-muted-foreground">
					{field.description}
					Default: {describeOption(field, defaultOf(field))}{rangeText(field)}.
				</p>
			</div>
		{/each}

		{#if unknownKeys.length > 0}
			<div
				class="space-y-2 rounded-md border border-warning/30 bg-warning/10 px-3 py-2.5 text-xs"
				role="alert"
			>
				<p class="flex items-center gap-1.5 font-medium text-warning-fg">
					<TriangleAlert class="size-3.5 shrink-0" aria-hidden="true" />
					Options this tool does not know
				</p>
				<p class="text-muted-foreground">
					Validation rejects them; remove them to save the profile.
				</p>
				<ul class="flex flex-wrap gap-1.5">
					{#each unknownKeys as key (key)}
						<li
							class="inline-flex items-center gap-1 rounded-full border border-border bg-background py-0.5 pl-2 pr-1 font-mono"
						>
							{key}{#if errors[key]}<span class="font-sans text-danger-fg">· {errors[key]}</span
								>{/if}
							{#if !readonly}
								<button
									type="button"
									class="flex size-4 cursor-pointer items-center justify-center rounded-full text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
									aria-label="Remove option {key}"
									onclick={() => removeUnknown(key)}
								>
									<X class="size-3" />
								</button>
							{/if}
						</li>
					{/each}
				</ul>
			</div>
		{/if}
	</div>
{/if}
