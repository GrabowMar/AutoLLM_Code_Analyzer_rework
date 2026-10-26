<script lang="ts">
	/**
	 * Data tab: every stored field — the raw OpenRouter payload, the structured
	 * metadata (sync fields, models.dev, Hugging Face, Ollama) and the database
	 * row — with nested values shown in full, plus copy / export.
	 */
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import Copy from '@lucide/svelte/icons/copy';
	import Download from '@lucide/svelte/icons/download';
	import type { LLMModelDetail } from '$lib/api/client';
	import { copyToClipboard, exportJson, getCaps, getMeta } from './helpers';

	interface Props {
		model: LLMModelDetail;
	}

	let { model }: Props = $props();

	const caps = $derived(getCaps(model));
	const meta = $derived(getMeta(model));
	const row = $derived(
		Object.fromEntries(Object.entries(model).filter(([k]) => k !== 'capabilities_json' && k !== 'metadata')) as Record<string, unknown>
	);

	const groups = $derived([
		{ id: 'payload', title: 'OpenRouter payload', source: 'capabilities_json', data: caps },
		{ id: 'metadata', title: 'Structured metadata', source: 'metadata', data: meta },
		{ id: 'row', title: 'Database row', source: `LLMModel #${model.id}`, data: row },
	]);

	function isEmpty(v: unknown): boolean {
		return v === null || v === undefined || v === '' || (Array.isArray(v) && v.length === 0) || (typeof v === 'object' && v !== null && Object.keys(v).length === 0);
	}
</script>

<div class="space-y-4">
	<div class="flex flex-wrap items-center justify-end gap-2">
		<Button variant="outline" size="sm" onclick={() => copyToClipboard(JSON.stringify(model, null, 2))}><Copy class="size-3.5" />Copy JSON</Button>
		<Button variant="outline" size="sm" onclick={() => exportJson(model)}><Download class="size-3.5" />Export</Button>
	</div>

	{#each groups as g (g.id)}
		{@const entries = Object.entries(g.data).sort(([a], [b]) => a.localeCompare(b))}
		<Card.Root class="gap-0 overflow-clip py-0">
			<Card.Header class="border-b border-border py-3">
				<Card.Title class="flex items-center gap-2 text-sm font-medium">
					{g.title}
					<Badge variant="outline" class="text-[11px]">{entries.length} keys</Badge>
					<span class="font-mono text-[11px] font-normal text-muted-foreground">{g.source}</span>
				</Card.Title>
			</Card.Header>
			<Card.Content class="p-0">
				<div class="overflow-x-auto">
					<table>
						<tbody>
							{#each entries as [key, value] (key)}
								<tr class="align-top">
									<td class="w-1/4 whitespace-nowrap font-mono text-xs text-muted-foreground">{key}</td>
									<td class="text-xs">
										{#if isEmpty(value)}
											<span class="text-muted-foreground">—</span>
										{:else if typeof value === 'object'}
											<pre class="max-h-64 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed">{JSON.stringify(value, null, 2)}</pre>
										{:else}
											<span class="break-words {typeof value === 'string' && value.length > 120 ? '' : 'font-mono'}">{String(value)}</span>
										{/if}
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</Card.Content>
		</Card.Root>
	{/each}
</div>
