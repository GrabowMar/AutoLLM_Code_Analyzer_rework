<script lang="ts">
	/**
	 * EndpointListEditor — an app spec's API contract: method, path, description
	 * and optional request/response JSON samples per endpoint. The conformance
	 * suite calls every endpoint and checks the response fields, and synthesises
	 * request bodies from the samples.
	 *
	 * Props: `endpoints` (bindable, replaced on edit), `prefix` (field-id prefix
	 * matching `specIssues`), `label`, `readonly`, `problem(fieldId)` → message.
	 */
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Textarea } from '$lib/components/ui/textarea';
	import * as Select from '$lib/components/ui/select';
	import Plus from '@lucide/svelte/icons/plus';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import { HTTP_METHODS, emptyEndpoint, type EndpointDraft, type HttpMethod } from './spec';

	interface Props {
		endpoints: EndpointDraft[];
		prefix: 'ep' | 'aep';
		label: string;
		readonly?: boolean;
		problem?: (fieldId: string) => string;
	}
	let { endpoints = $bindable(), prefix, label, readonly = false, problem = () => '' }: Props = $props();

	const methodItems = HTTP_METHODS.map((m) => ({ value: m, label: m }));
	// Rows whose request/response samples are shown; new rows open.
	let open = $state<Record<number, boolean>>({});

	function patch(key: number, change: Partial<EndpointDraft>) {
		endpoints = endpoints.map((e) => (e.key === key ? { ...e, ...change } : e));
	}

	function add() {
		const e = emptyEndpoint(endpoints.length ? 'POST' : 'GET');
		endpoints = [...endpoints, e];
		open = { ...open, [e.key]: true };
	}

	function remove(key: number) {
		endpoints = endpoints.filter((e) => e.key !== key);
	}
</script>

<div class="space-y-2">
	{#if endpoints.length === 0}
		<p class="rounded-md border border-dashed border-border px-3 py-3 text-xs text-muted-foreground">
			No {label.toLowerCase()}.
		</p>
	{/if}
	{#each endpoints as e, i (e.key)}
		{@const id = `${prefix}-${e.key}`}
		{@const pathProblem = problem(`${id}-path`)}
		{@const descProblem = problem(`${id}-desc`)}
		{@const reqProblem = problem(`${id}-request`)}
		{@const resProblem = problem(`${id}-response`)}
		{@const expanded = open[e.key] || !!reqProblem || !!resProblem}
		<div class="rounded-md border border-border bg-muted/10">
			<div class="flex flex-wrap items-start gap-2 p-2">
				<button
					type="button"
					class="mt-1.5 cursor-pointer text-muted-foreground transition-transform duration-150 hover:text-foreground {expanded ? 'rotate-90' : ''}"
					aria-expanded={expanded}
					aria-controls="{id}-samples"
					aria-label="{expanded ? 'Hide' : 'Show'} request and response samples for {label.toLowerCase()} {i + 1}"
					onclick={() => (open = { ...open, [e.key]: !open[e.key] })}
				>
					<ChevronRight class="size-4" />
				</button>
				<div class="w-[6.5rem] shrink-0">
					{#if readonly}
						<span class="inline-block pt-1.5 font-mono text-xs font-medium">{e.method}</span>
					{:else}
						<Select.Simple
							size="sm"
							value={e.method}
							items={methodItems}
							aria-label="{label} {i + 1} method"
							onValueChange={(v) => patch(e.key, { method: v as HttpMethod })}
						/>
					{/if}
				</div>
				<div class="min-w-[10rem] flex-1 space-y-1">
					<Input
						id="{id}-path"
						value={e.path}
						readonly={readonly}
						aria-label="{label} {i + 1} path"
						aria-invalid={!!pathProblem}
						placeholder="/api/items/:id"
						class="h-8 font-mono text-xs"
						oninput={(ev) => patch(e.key, { path: ev.currentTarget.value })}
					/>
					{#if pathProblem}<p class="text-[11px] text-danger-fg">{pathProblem}</p>{/if}
				</div>
				<div class="min-w-[12rem] flex-[2] space-y-1">
					<Input
						id="{id}-desc"
						value={e.description}
						readonly={readonly}
						aria-label="{label} {i + 1} description"
						aria-invalid={!!descProblem}
						placeholder="What it does"
						class="h-8 text-xs"
						oninput={(ev) => patch(e.key, { description: ev.currentTarget.value })}
					/>
					{#if descProblem}<p class="text-[11px] text-danger-fg">{descProblem}</p>{/if}
				</div>
				{#if !readonly}
					<Button
						variant="ghost"
						size="sm"
						class="size-8 p-0 text-muted-foreground hover:bg-destructive/10 hover:text-danger-fg"
						aria-label="Remove {label.toLowerCase()} {i + 1}"
						title="Remove"
						onclick={() => remove(e.key)}
					>
						<Trash2 />
					</Button>
				{/if}
			</div>
			{#if expanded}
				<div id="{id}-samples" class="grid gap-2 border-t border-border p-2 sm:grid-cols-2">
					<div class="space-y-1">
						<label for="{id}-request" class="section-label">Request sample (JSON)</label>
						<Textarea
							id="{id}-request"
							value={e.request}
							readonly={readonly}
							rows={5}
							aria-invalid={!!reqProblem}
							placeholder={'{"title": "string"}'}
							class="bg-background font-mono text-[11px] leading-normal"
							oninput={(ev: Event & { currentTarget: HTMLTextAreaElement }) => patch(e.key, { request: ev.currentTarget.value })}
						/>
						{#if reqProblem}<p class="text-[11px] text-danger-fg">{reqProblem}</p>{/if}
					</div>
					<div class="space-y-1">
						<label for="{id}-response" class="section-label">Response sample (JSON)</label>
						<Textarea
							id="{id}-response"
							value={e.response}
							readonly={readonly}
							rows={5}
							aria-invalid={!!resProblem}
							placeholder={'{"id": 1, "title": "string"}'}
							class="bg-background font-mono text-[11px] leading-normal"
							oninput={(ev: Event & { currentTarget: HTMLTextAreaElement }) => patch(e.key, { response: ev.currentTarget.value })}
						/>
						{#if resProblem}<p class="text-[11px] text-danger-fg">{resProblem}</p>{/if}
					</div>
				</div>
			{/if}
		</div>
	{/each}
	{#if !readonly}
		<Button variant="outline" size="sm" onclick={add}><Plus /> Add endpoint</Button>
	{/if}
</div>
