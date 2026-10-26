<script lang="ts">
	/**
	 * RecentRunsTable — the latest analysis runs on the dashboard: name and
	 * target, status (with "incomplete" when a required tool did not
	 * succeed, progress while running) and time.
	 *
	 * Props: `runs` (list items, newest first), `loading`.
	 */
	import type { AnalysisRunListItem } from '$lib/api/analysis';
	import { isRunActive } from '$lib/api/analysis';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import { fmtAgo, fmtDateTime, toolProgress } from '$lib/components/analysis/run-utils';
	import ArrowRight from '@lucide/svelte/icons/arrow-right';
	import Microscope from '@lucide/svelte/icons/microscope';

	interface Props {
		runs: AnalysisRunListItem[];
		loading?: boolean;
	}

	let { runs, loading = false }: Props = $props();
</script>

<Card.Root>
	<Card.Header>
		<div class="flex items-center justify-between">
			<div class="flex items-center gap-2">
				<Microscope class="h-4 w-4 text-muted-foreground" aria-hidden="true" />
				<div>
					<Card.Title>Recent Analyses</Card.Title>
					<Card.Description>Latest analysis runs and their status.</Card.Description>
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
			<table class="table-card-mobile w-full">
				<thead>
					<tr class="border-b bg-muted/30">
						<th class="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Run</th>
						<th class="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Status</th>
						<th class="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Time</th>
					</tr>
				</thead>
				<tbody class="divide-y">
					{#each runs as run (run.id)}
						{@const active = isRunActive(run.status)}
						{@const progress = toolProgress(run)}
						<tr class="transition-colors hover:bg-muted/30">
							<td class="max-w-[16rem] px-4 py-2.5" data-label="Run">
								<a
									href="/analysis/{run.id}"
									class="block truncate text-sm font-medium hover:text-primary hover:underline"
									title={run.name}>{run.name}</a
								>
								<span
									class="block truncate font-mono text-[11px] text-muted-foreground"
									title={run.target.label}>{run.target.label}</span
								>
							</td>
							<td class="px-4 py-2.5" data-label="Status">
								<span class="flex flex-wrap items-center gap-1">
									<StatusBadge status={run.status} />
									{#if run.status === 'completed' && !run.is_complete}
										<StatusBadge status="incomplete" tone="warning" />
									{/if}
								</span>
								{#if active}
									<span class="text-[11px] text-muted-foreground"
										>{progress.done}/{progress.total} tools</span
									>
								{/if}
							</td>
							<td class="px-4 py-2.5" data-label="Time">
								<span class="text-xs text-muted-foreground" title={fmtDateTime(run.created_at)}
									>{fmtAgo(run.created_at)}</span
								>
							</td>
						</tr>
					{:else}
						<tr>
							<td colspan="3">
								{#if loading}
									<p class="px-4 py-6 text-center text-xs text-muted-foreground" role="status">
										Loading…
									</p>
								{:else}
									<EmptyState compact icon={Microscope} title="No analyses yet">
										{#snippet action()}
											<Button href="/analysis/create" size="sm" variant="outline">Ad-hoc run</Button
											>
										{/snippet}
									</EmptyState>
								{/if}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</Card.Content>
</Card.Root>
