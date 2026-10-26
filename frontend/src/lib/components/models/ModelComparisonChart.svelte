<script lang="ts">
	/** Other models from the same provider and source (catalog or local), as a compact table. */
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import GitCompareArrows from '@lucide/svelte/icons/git-compare-arrows';
	import { rowLink, shortModelName } from '$lib/components/table';
	import type { LLMModelSummary } from '$lib/api/client';
	import { formatPrice } from './helpers';

	interface Props {
		provider: string;
		slug: string;
		relatedModels: LLMModelSummary[];
	}

	let { provider, slug, relatedModels }: Props = $props();

	const compareHref = $derived(`/models/compare?models=${[slug, ...relatedModels.slice(0, 3).map((m) => m.canonical_slug)].join(',')}`);
</script>

{#if relatedModels.length}
	<Card.Root class="gap-0 overflow-clip py-0">
		<Card.Header class="flex flex-row items-center justify-between gap-2 border-b border-border py-3">
			<div>
				<Card.Title class="text-sm font-medium">More from {provider}</Card.Title>
				<Card.Description class="text-xs">Best value first</Card.Description>
			</div>
			<Button variant="ghost" size="sm" href={compareHref}><GitCompareArrows class="size-3.5" />Compare top 3</Button>
		</Card.Header>
		<Card.Content class="p-0">
			<div class="overflow-x-auto">
				<table>
					<thead>
						<tr>
							<th>Model</th>
							<th class="num">Context</th>
							<th class="num">$ in / out per 1M</th>
							<th>Features</th>
						</tr>
					</thead>
					<tbody>
						{#each relatedModels as m (m.id)}
							{@const href = `/models/${m.canonical_slug}`}
							<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions (the name link is the keyboard path) -->
							<tr class="row-link" onclick={rowLink(href)}>
								<td class="max-w-[18rem]">
									<a {href} class="block truncate font-medium hover:text-primary" title={m.model_name}>{shortModelName(m.model_name)}</a>
									<span class="cell-sub block truncate font-mono text-[11px] text-muted-foreground">{m.model_id}</span>
								</td>
								<td class="num">{m.context_window_display}</td>
								<td class="num whitespace-nowrap">{m.is_free ? 'Free' : `${formatPrice(m.input_price_per_million)} / ${formatPrice(m.output_price_per_million)}`}</td>
								<td class="text-xs text-muted-foreground">
									{[m.supports_function_calling && 'tools', m.supports_vision && 'vision', m.supports_reasoning && 'reasoning', m.supports_json_mode && 'JSON'].filter(Boolean).join(' · ') || '—'}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</Card.Content>
	</Card.Root>
{/if}
