<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import AppWindow from '@lucide/svelte/icons/app-window';
	import ArrowRight from '@lucide/svelte/icons/arrow-right';
	import { generationStatusColors as statusColors } from '$lib/constants/colors';
	import type { RecentApp } from '$lib/utils/dashboard';

	interface Props {
		jobs: RecentApp[];
	}

	let { jobs }: Props = $props();
</script>

<Card.Root>
	<Card.Header>
		<div class="flex items-center justify-between">
			<div class="flex items-center gap-2">
				<AppWindow class="h-4 w-4 text-muted-foreground" />
				<div>
					<Card.Title>Recent Generation Jobs</Card.Title>
					<Card.Description>Latest application generation runs.</Card.Description>
				</div>
			</div>
			<Button variant="ghost" size="sm" href="/sample-generator" class="text-xs">
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
						<th class="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Job</th>
						<th class="hide-mobile px-4 py-2.5 text-left text-xs font-medium text-muted-foreground"
							>Model</th
						>
						<th class="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Status</th>
						<th class="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Created</th>
					</tr>
				</thead>
				<tbody class="divide-y">
					{#each jobs as app (app.id)}
						<tr class="transition-colors hover:bg-muted/30">
							<td class="px-4 py-2.5" data-label="Job">
								<span class="text-sm font-medium">{app.name}</span>
							</td>
							<td class="hide-mobile px-4 py-2.5" data-label="Model">
								<span class="text-sm text-muted-foreground">{app.model}</span>
							</td>
							<td class="px-4 py-2.5" data-label="Status">
								<span
									class="inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium {statusColors[
										app.status
									]}"
								>
									{app.status}
								</span>
							</td>
							<td class="px-4 py-2.5" data-label="Created">
								<span class="text-xs text-muted-foreground">{app.time}</span>
							</td>
						</tr>
					{:else}
						<tr>
							<td colspan="4" class="px-4 py-6 text-center text-sm text-muted-foreground">
								No generation jobs yet.
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</Card.Content>
</Card.Root>
