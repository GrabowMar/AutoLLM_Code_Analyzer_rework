<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import FlaskConical from '@lucide/svelte/icons/flask-conical';
	import ArrowRight from '@lucide/svelte/icons/arrow-right';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import type { RecentAnalysis } from '$lib/utils/dashboard';
	import { rowLink } from '$lib/components/table';

	interface Props {
		analyses: RecentAnalysis[];
	}

	let { analyses }: Props = $props();
</script>

<Card.Root class="gap-0 overflow-clip py-0">
	<Card.Header class="border-b border-border py-3">
		<div class="flex items-center justify-between">
			<div class="flex items-center gap-2">
				<FlaskConical class="h-4 w-4 text-muted-foreground" />
				<div>
					<Card.Title>Recent Analyses</Card.Title>
					<Card.Description>Latest analysis tasks and their status.</Card.Description>
				</div>
			</div>
			<Button variant="ghost" size="sm" href="/analysis" class="text-xs">
				View all
				<ArrowRight class="ml-1 h-3 w-3" />
			</Button>
		</div>
	</Card.Header>
	<Card.Content class="p-0">
		<div class="overflow-x-auto">
			<table class="table-card-mobile">
				<thead>
					<tr>
						<th>Task</th>
						<th>Status</th>
						<th>Time</th>
					</tr>
				</thead>
				<tbody>
					{#each analyses as analysis (analysis.id)}
						{@const href = `/analysis/${analysis.id}`}
						<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions (the name link is the keyboard path) -->
						<tr class="row-link" onclick={rowLink(href)}>
							<td data-label="Task">
								<a {href} class="block max-w-[26rem] truncate font-medium transition-colors hover:text-primary" title={analysis.name}>{analysis.name}</a>
							</td>
							<td class="whitespace-nowrap" data-label="Status">
								<StatusBadge status={analysis.status} />
							</td>
							<td class="whitespace-nowrap" data-label="Time">
								<span class="text-xs text-muted-foreground">{analysis.time}</span>
							</td>
						</tr>
					{:else}
						<tr>
							<td colspan="3">
								<EmptyState compact icon={FlaskConical} title="No analyses yet">
									{#snippet action()}
										<Button href="/analysis/create" size="sm" variant="outline">New analysis</Button>
									{/snippet}
								</EmptyState>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</Card.Content>
</Card.Root>
