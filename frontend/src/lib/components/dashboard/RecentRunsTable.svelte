<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import FlaskConical from '@lucide/svelte/icons/flask-conical';
	import ArrowRight from '@lucide/svelte/icons/arrow-right';
	import { generationStatusColors as statusColors } from '$lib/constants/colors';
	import type { RecentAnalysis } from '$lib/utils/dashboard';

	interface Props {
		analyses: RecentAnalysis[];
	}

	let { analyses }: Props = $props();
</script>

<Card.Root>
	<Card.Header>
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
			<table class="table-card-mobile w-full">
				<thead>
					<tr class="border-b bg-muted/30">
						<th class="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Task</th>
						<th class="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Status</th>
						<th class="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Time</th>
					</tr>
				</thead>
				<tbody class="divide-y">
					{#each analyses as analysis (analysis.id)}
						<tr class="transition-colors hover:bg-muted/30">
							<td class="px-4 py-2.5" data-label="Task">
								<a
									href="/analysis/{analysis.id}"
									class="text-sm font-medium hover:text-primary hover:underline"
									>{analysis.name}</a
								>
							</td>
							<td class="px-4 py-2.5" data-label="Status">
								<span
									class="inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium {statusColors[
										analysis.status
									]}"
								>
									{analysis.status}
								</span>
							</td>
							<td class="px-4 py-2.5" data-label="Time">
								<span class="text-xs text-muted-foreground">{analysis.time}</span>
							</td>
						</tr>
					{:else}
						<tr>
							<td colspan="3" class="px-4 py-6 text-center text-sm text-muted-foreground">
								No analyses yet — start one above.
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</Card.Content>
</Card.Root>
