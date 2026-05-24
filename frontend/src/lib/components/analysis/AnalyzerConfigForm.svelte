<script lang="ts">
import type { AnalyzerInfo, ConfigField } from '$lib/api/analysis';

interface Props {
	analyzer: AnalyzerInfo;
	config: Record<string, unknown>;
	onConfigChange: (key: string, value: unknown) => void;
}

let { analyzer, config, onConfigChange }: Props = $props();

function getValue(field: ConfigField): unknown {
	return config[field.name] ?? field.default;
}

function handleChange(field: ConfigField, value: unknown) {
	onConfigChange(field.name, value);
}

function handleMultiSelect(field: ConfigField, optionValue: string, checked: boolean) {
	const current = (getValue(field) as string[]) ?? [];
	const next = checked ? [...current, optionValue] : current.filter((v) => v !== optionValue);
	onConfigChange(field.name, next);
}

function isMultiSelected(field: ConfigField, optionValue: string): boolean {
	const current = (getValue(field) as string[]) ?? [];
	return current.includes(optionValue);
}
</script>

{#if analyzer.config_schema.length === 0}
	<p class="text-xs text-muted-foreground italic">No configurable options for this analyzer.</p>
{:else}
	<div class="space-y-3">
		{#each analyzer.config_schema as field}
			<div>
				<label class="mb-1 block text-sm font-medium" for={`${analyzer.name}-${field.name}`}>
					{field.label}
					{#if field.required}
						<span class="ml-0.5 text-red-500">*</span>
					{/if}
				</label>

				{#if field.type === 'boolean'}
					<div class="flex items-center gap-2">
						<input
							id={`${analyzer.name}-${field.name}`}
							type="checkbox"
							class="rounded border-input"
							checked={Boolean(getValue(field))}
							onchange={(e) => handleChange(field, (e.target as HTMLInputElement).checked)}
						/>
						<span class="text-sm text-muted-foreground">{field.description}</span>
					</div>

				{:else if field.type === 'number'}
					<input
						id={`${analyzer.name}-${field.name}`}
						type="number"
						class="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
						value={getValue(field) as number}
						min={field.min ?? undefined}
						max={field.max ?? undefined}
						placeholder={field.placeholder || String(field.default ?? '')}
						oninput={(e) => handleChange(field, Number((e.target as HTMLInputElement).value))}
					/>
					{#if field.description}
						<p class="mt-0.5 text-xs text-muted-foreground">{field.description}</p>
					{/if}

				{:else if field.type === 'select'}
					<select
						id={`${analyzer.name}-${field.name}`}
						class="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
						value={getValue(field) as string}
						onchange={(e) => handleChange(field, (e.target as HTMLSelectElement).value)}
					>
						{#each field.options as opt}
							<option value={opt.value}>{opt.label}</option>
						{/each}
					</select>
					{#if field.description}
						<p class="mt-0.5 text-xs text-muted-foreground">{field.description}</p>
					{/if}

				{:else if field.type === 'multiselect'}
					<div class="rounded-md border border-input bg-background p-2 space-y-1.5">
						{#each field.options as opt}
							<label class="flex items-center gap-2 text-sm cursor-pointer">
								<input
									type="checkbox"
									class="rounded border-input"
									checked={isMultiSelected(field, opt.value)}
									onchange={(e) => handleMultiSelect(field, opt.value, (e.target as HTMLInputElement).checked)}
								/>
								{opt.label}
							</label>
						{/each}
					</div>
					{#if field.description}
						<p class="mt-0.5 text-xs text-muted-foreground">{field.description}</p>
					{/if}

				{:else}
					<input
						id={`${analyzer.name}-${field.name}`}
						type="text"
						class="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
						value={getValue(field) as string}
						placeholder={field.placeholder || String(field.default ?? '')}
						oninput={(e) => handleChange(field, (e.target as HTMLInputElement).value)}
					/>
					{#if field.description}
						<p class="mt-0.5 text-xs text-muted-foreground">{field.description}</p>
					{/if}
				{/if}
			</div>
		{/each}
	</div>
{/if}
