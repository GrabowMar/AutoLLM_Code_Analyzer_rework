<script lang="ts">
	/**
	 * Benchmark scores published in the model's Hugging Face repo (evalResults /
	 * model-index): self-reported by the authors or third parties, shown exactly
	 * as published with their source and harness notes. Not verified here, not
	 * normalized (scales differ) and never folded into any platform score.
	 */
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import InheritedNote from './InheritedNote.svelte';
	import { isoDay, type HfEvalResult } from './helpers';

	interface Props {
		results: HfEvalResult[];
		/** Published count before the storage cap; more than `results.length` when rows were cut. */
		total?: number | null;
		hfId: string;
		fetchedAt?: string | null;
		inheritedFrom?: string | null;
		inheritedSlug?: string | null;
	}

	let { results, total = null, hfId, fetchedAt = null, inheritedFrom = null, inheritedSlug = null }: Props = $props();

	const truncated = $derived(total != null && total > results.length);

	const sorted = $derived([...results].sort((a, b) => (a.dataset ?? '').localeCompare(b.dataset ?? '')));
	const verified = $derived(results.filter((r) => r.verified).length);
</script>

<Card.Root class="gap-0 overflow-clip py-0">
	<Card.Header class="border-b border-border py-3">
		<Card.Title class="flex items-center gap-2 text-sm font-medium">
			Self-reported evaluations
			<Badge variant="outline">self-reported</Badge>
			<a href="https://huggingface.co/{hfId}" target="_blank" rel="noopener noreferrer" class="text-muted-foreground hover:text-foreground" aria-label="Model repo on Hugging Face"><ExternalLink class="size-3.5" /></a>
		</Card.Title>
		<Card.Description class="text-xs">
			{#if truncated}First {results.length} of {total}{:else}{results.length}{/if} scores published in <span class="font-mono">{hfId}</span> ({verified} verified by Hugging Face{truncated ? ' among those shown' : ''}){fetchedAt ? ` · fetched ${isoDay(fetchedAt)}` : ''}. As published: not checked by this platform, scales differ between rows (0–1 vs 0–100), harnesses differ.
		</Card.Description>
		<InheritedNote from={inheritedFrom} slug={inheritedSlug} />
	</Card.Header>
	<Card.Content class="p-0">
		<div class="overflow-x-auto">
			<table>
				<thead>
					<tr><th>Dataset · task</th><th class="num">Score</th><th>Source</th><th class="num">Date</th></tr>
				</thead>
				<tbody>
					{#each sorted as r, i (i)}
						<tr class="align-top">
							<td class="max-w-[22rem]">
								<span class="font-mono text-xs break-words">{r.dataset ?? '—'}</span>
								{#if r.task && r.task !== r.dataset}<span class="cell-sub block text-[11px] text-muted-foreground">{r.task}{r.metric && r.metric !== r.task ? ` · ${r.metric}` : ''}</span>{/if}
								{#if r.notes}<span class="cell-sub block text-[11px] text-muted-foreground">{r.notes}</span>{/if}
							</td>
							<td class="num">{r.value}</td>
							<td class="text-xs">
								{#if r.source_url}
									<a href={r.source_url} target="_blank" rel="noopener noreferrer" class="text-primary hover:underline">{r.source_name ?? 'source'}</a>
								{:else}
									{r.source_name ?? r.source}
								{/if}
								{#if r.verified}<Badge variant="secondary" class="ml-1">verified</Badge>{/if}
							</td>
							<td class="num text-xs text-muted-foreground">{r.date ? r.date.slice(0, 10) : '—'}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</Card.Content>
</Card.Root>
