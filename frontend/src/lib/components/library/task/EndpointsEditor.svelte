<script lang="ts">
	/**
	 * Endpoints of a task: one collapsible row per endpoint (`METHOD /path · id · auth`), expanded to edit
	 * the id, method, path, auth and owner entity, success status, content type, realtime, promised error
	 * statuses, tags, query parameters and the request/response JSON Schemas. Edits the bound draft.
	 */
	import * as Select from '$lib/components/ui/select';
	import { Input } from '$lib/components/ui/input';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Button } from '$lib/components/ui/button';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import Plus from '@lucide/svelte/icons/plus';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import { CONTENT_TYPES, ENDPOINT_AUTHS, FIELD_TYPES, HTTP_METHODS, QUERY_SEMANTICS, REALTIME_KINDS } from '$lib/api/v3/types';
	import NumberInput from '../NumberInput.svelte';
	import FormRow from '../FormRow.svelte';
	import { newEndpoint, newQuery, type EndpointDraft, type TaskErrors } from '../task';

	interface Props {
		endpoints: EndpointDraft[];
		entityNames: string[];
		errors?: TaskErrors;
		readonly?: boolean;
	}

	let { endpoints = $bindable(), entityNames, errors = {}, readonly = false }: Props = $props();

	let open = $state<Record<number, boolean>>({});
	const items = (xs: readonly string[]) => xs.map((x) => ({ value: x, label: x }));
	const errorAt = (i: number, key: string) => errors[`endpoints.${i}.${key}`];
	const hasError = (i: number) => Object.keys(errors).some((k) => k.startsWith(`endpoints.${i}.`));

	function add() {
		endpoints = [...endpoints, newEndpoint()];
		open = { ...open, [endpoints.length - 1]: true };
	}
</script>

<div class="space-y-2">
	{#if endpoints.length === 0}
		<p class="text-sm text-muted-foreground">No endpoints yet.</p>
	{/if}
	<ul class="divide-y divide-border rounded-md border border-border">
		{#each endpoints as ep, i (i)}
			{@const isOpen = open[i] ?? false}
			<li>
				<button
					type="button"
					class="flex w-full cursor-pointer items-center gap-2 px-3 py-2 text-left hover:bg-muted/50"
					aria-expanded={isOpen}
					onclick={() => (open = { ...open, [i]: !isOpen })}
				>
					<ChevronRight class="size-4 shrink-0 text-muted-foreground transition-transform duration-150 {isOpen ? 'rotate-90' : ''}" aria-hidden="true" />
					<span class="w-14 font-mono text-xs font-medium">{ep.method}</span>
					<span class="min-w-0 flex-1 truncate font-mono text-xs">{ep.path}</span>
					<span class="font-mono text-[11px] text-muted-foreground">{ep.id || 'no id'}</span>
					<span class="rounded-sm border border-border px-1 text-[11px] text-muted-foreground">{ep.auth}</span>
					{#if hasError(i)}<span class="text-[11px] text-danger-fg">fix</span>{/if}
				</button>
				{#if isOpen}
					<div class="space-y-3 border-t border-border/70 bg-muted/10 px-3 py-3">
						<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
							<FormRow label="Id" help="Stable dotted id; checks, probes and slots refer to it.">
								<Input bind:value={ep.id} placeholder="todos.update" class="h-8 font-mono" readonly={readonly} />
							</FormRow>
							<FormRow label="Method">
								<Select.Simple value={ep.method} items={items(HTTP_METHODS)} size="sm" disabled={readonly} onValueChange={(v) => (ep.method = v as typeof ep.method)} />
							</FormRow>
							<FormRow label="Path" help="Starts with /; parameters as {'{id}'}." class="lg:col-span-2">
								<Input bind:value={ep.path} class="h-8 font-mono" readonly={readonly} />
							</FormRow>
							<FormRow label="Auth">
								<Select.Simple value={ep.auth} items={items(ENDPOINT_AUTHS)} size="sm" disabled={readonly} onValueChange={(v) => (ep.auth = v as typeof ep.auth)} />
							</FormRow>
							<FormRow label="Owner entity" help="Required for owner-scoped endpoints." error={errorAt(i, 'owner_entity')}>
								<Select.Simple
									value={ep.owner_entity}
									items={items(entityNames)}
									clearLabel="None"
									placeholder="None"
									size="sm"
									disabled={readonly}
									onValueChange={(v) => (ep.owner_entity = v)}
								/>
							</FormRow>
							<FormRow label="Success status">
								<NumberInput value={ep.success_status} min={200} max={299} step={1} {readonly} onchange={(n) => n !== null && (ep.success_status = n)} />
							</FormRow>
							<FormRow label="Content type">
								<Select.Simple value={ep.content_type} items={items(CONTENT_TYPES)} size="sm" disabled={readonly} onValueChange={(v) => (ep.content_type = v as typeof ep.content_type)} />
							</FormRow>
							<FormRow label="Promised errors" help="Statuses the spec promises: 400, 401, 403, 404, 409…" error={errorAt(i, 'errors')}>
								<Input bind:value={ep.errors_text} placeholder="400, 404" class="h-8 font-mono" readonly={readonly} />
							</FormRow>
							<FormRow label="Tags">
								<Input bind:value={ep.tags_text} placeholder="none" class="h-8 font-mono" readonly={readonly} />
							</FormRow>
							<FormRow label="Realtime">
								<Select.Simple value={ep.realtime} items={items(REALTIME_KINDS)} size="sm" disabled={readonly} onValueChange={(v) => (ep.realtime = v as typeof ep.realtime)} />
							</FormRow>
						</div>

						<div class="space-y-1.5">
							<p class="section-label">Query parameters</p>
							{#if ep.query.length}
								<div class="overflow-x-auto">
									<table class="w-full text-xs">
										<thead><tr><th>Name</th><th>Type</th><th>Semantics</th><th>Req.</th><th>Enum</th><th>Default</th><th>Max</th><th><span class="sr-only">Actions</span></th></tr></thead>
										<tbody>
											{#each ep.query as q, qi (qi)}
												<tr>
													<td><Input bind:value={q.name} class="h-8 w-28 font-mono" aria-label="Parameter name" readonly={readonly} /></td>
													<td><Select.Simple value={q.type} items={items(FIELD_TYPES)} size="sm" class="w-24" disabled={readonly} aria-label="Type" onValueChange={(v) => (q.type = v as typeof q.type)} /></td>
													<td><Select.Simple value={q.semantics} items={items(QUERY_SEMANTICS)} size="sm" class="w-28" disabled={readonly} aria-label="Semantics" onValueChange={(v) => (q.semantics = v as typeof q.semantics)} /></td>
													<td><Checkbox bind:checked={q.required} disabled={readonly} aria-label="Required" /></td>
													<td><Input bind:value={q.enum_text} class="h-8 w-32 font-mono" aria-label="Enum values" readonly={readonly} /></td>
													<td><Input bind:value={q.default_text} class="h-8 w-20 font-mono" aria-label="Default" readonly={readonly} /></td>
													<td><NumberInput value={q.max} class="w-20" {readonly} aria-label="Max" onchange={(n) => (q.max = n)} /></td>
													<td>
														{#if !readonly}
															<Button variant="ghost" size="icon" aria-label="Remove parameter" onclick={() => (ep.query = ep.query.filter((_, j) => j !== qi))}><Trash2 /></Button>
														{/if}
													</td>
												</tr>
											{/each}
										</tbody>
									</table>
								</div>
							{/if}
							{#if !readonly}
								<Button variant="outline" size="sm" onclick={() => (ep.query = [...ep.query, newQuery()])}><Plus />Add parameter</Button>
							{/if}
						</div>

						<div class="grid gap-3 lg:grid-cols-2">
							<FormRow label="Request schema" help="JSON Schema (2020-12 subset); empty = no body." error={errorAt(i, 'request_schema')}>
								<textarea
									bind:value={ep.request_schema_text}
									readonly={readonly}
									rows={8}
									spellcheck="false"
									class="block w-full rounded-md border border-input bg-surface-1 px-2 py-1.5 font-mono text-xs read-only:bg-muted/30"
								></textarea>
							</FormRow>
							<FormRow label="Response schema" help="JSON Schema of the success body; empty = unchecked." error={errorAt(i, 'response_schema')}>
								<textarea
									bind:value={ep.response_schema_text}
									readonly={readonly}
									rows={8}
									spellcheck="false"
									class="block w-full rounded-md border border-input bg-surface-1 px-2 py-1.5 font-mono text-xs read-only:bg-muted/30"
								></textarea>
							</FormRow>
						</div>
						{#if !readonly}
							<div class="flex justify-end">
								<Button variant="ghost" size="sm" onclick={() => (endpoints = endpoints.filter((_, j) => j !== i))}><Trash2 />Remove endpoint</Button>
							</div>
						{/if}
					</div>
				{/if}
			</li>
		{/each}
	</ul>
	{#if !readonly}
		<Button variant="outline" size="sm" onclick={add}><Plus />Add endpoint</Button>
	{/if}
</div>
