<script lang="ts">
	/**
	 * Entities of a task: name, ownership, soft delete and the fields (type, constraints, enum values,
	 * reference target, default, secret). Edits the bound `entities` draft in place.
	 */
	import * as Select from '$lib/components/ui/select';
	import { Input } from '$lib/components/ui/input';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Button } from '$lib/components/ui/button';
	import Plus from '@lucide/svelte/icons/plus';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import { FIELD_TYPES } from '$lib/api/v3/types';
	import NumberInput from '../NumberInput.svelte';
	import { newEntity, newField, type EntityDraft } from '../task';

	interface Props {
		entities: EntityDraft[];
		readonly?: boolean;
	}

	let { entities = $bindable(), readonly = false }: Props = $props();

	const typeItems = FIELD_TYPES.map((t) => ({ value: t, label: t }));
</script>

<div class="space-y-4">
	{#if entities.length === 0}
		<p class="text-sm text-muted-foreground">No entities. Most tasks have a User and one or more domain entities.</p>
	{/if}
	{#each entities as entity, ei (ei)}
		<section class="rounded-md border border-border" aria-label="Entity {entity.name || ei + 1}">
			<header class="flex flex-wrap items-center gap-3 border-b border-border bg-muted/30 px-3 py-2">
				<Input bind:value={entity.name} placeholder="EntityName" class="h-8 w-48 font-mono" aria-label="Entity name" readonly={readonly} />
				<label class="flex items-center gap-1.5 text-xs"><Checkbox bind:checked={entity.owned_by_user} disabled={readonly} /> owned by user</label>
				<label class="flex items-center gap-1.5 text-xs"><Checkbox bind:checked={entity.soft_delete} disabled={readonly} /> soft delete</label>
				{#if !readonly}
					<Button variant="ghost" size="sm" class="ml-auto" onclick={() => (entities = entities.filter((_, j) => j !== ei))}><Trash2 />Remove entity</Button>
				{/if}
			</header>
			<div class="overflow-x-auto">
				<table class="w-full text-xs">
					<thead>
						<tr>
							<th>Field</th>
							<th>Type</th>
							<th>Req.</th>
							<th>Uniq.</th>
							<th>Secret</th>
							<th>Max len</th>
							<th>Min</th>
							<th>Max</th>
							<th>Enum / ref</th>
							<th>Default (JSON)</th>
							<th><span class="sr-only">Actions</span></th>
						</tr>
					</thead>
					<tbody>
						{#each entity.fields as field, fi (fi)}
							<tr>
								<td><Input bind:value={field.name} class="h-8 w-36 font-mono" aria-label="Field name" readonly={readonly} /></td>
								<td>
									<Select.Simple
										value={field.type}
										items={typeItems}
										size="sm"
										class="w-28"
										disabled={readonly}
										aria-label="Field type"
										onValueChange={(v) => (field.type = v as typeof field.type)}
									/>
								</td>
								<td><Checkbox bind:checked={field.required} disabled={readonly} aria-label="Required" /></td>
								<td><Checkbox bind:checked={field.unique} disabled={readonly} aria-label="Unique" /></td>
								<td><Checkbox bind:checked={field.secret} disabled={readonly} aria-label="Secret (never echoed)" /></td>
								<td><NumberInput value={field.max_length} min={1} step={1} class="w-20" {readonly} aria-label="Max length" onchange={(n) => (field.max_length = n)} /></td>
								<td><NumberInput value={field.min} class="w-20" {readonly} aria-label="Minimum" onchange={(n) => (field.min = n)} /></td>
								<td><NumberInput value={field.max} class="w-20" {readonly} aria-label="Maximum" onchange={(n) => (field.max = n)} /></td>
								<td>
									{#if field.type === 'enum'}
										<Input bind:value={field.enum_text} placeholder="low, medium, high" class="h-8 w-40 font-mono" aria-label="Enum values" readonly={readonly} />
									{:else if field.type === 'ref'}
										<Input bind:value={field.ref} placeholder="Entity" class="h-8 w-32 font-mono" aria-label="Referenced entity" readonly={readonly} />
									{:else}
										<span class="text-muted-foreground">—</span>
									{/if}
								</td>
								<td><Input bind:value={field.default_text} placeholder="null" class="h-8 w-28 font-mono" aria-label="Default" readonly={readonly} /></td>
								<td>
									{#if !readonly}
										<Button variant="ghost" size="icon" aria-label="Remove field" onclick={() => (entity.fields = entity.fields.filter((_, j) => j !== fi))}><Trash2 /></Button>
									{/if}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			{#if !readonly}
				<div class="border-t border-border px-3 py-2">
					<Button variant="outline" size="sm" onclick={() => (entity.fields = [...entity.fields, newField()])}><Plus />Add field</Button>
				</div>
			{/if}
		</section>
	{/each}
	{#if !readonly}
		<Button variant="outline" size="sm" onclick={() => (entities = [...entities, newEntity()])}><Plus />Add entity</Button>
	{/if}
</div>
