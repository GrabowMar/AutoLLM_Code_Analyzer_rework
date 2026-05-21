<script lang="ts">
import * as Card from '$lib/components/ui/card';
import { Badge } from '$lib/components/ui/badge';
import Package from '@lucide/svelte/icons/package';

interface Props {
	deps: (string | unknown)[];
	mode: string;
}

let { deps, mode }: Props = $props();
</script>

<section id="dependencies" class="space-y-4">
	<h2 class="text-lg font-semibold flex items-center gap-2"><Package class="h-5 w-5" /> Dependencies</h2>
	{#if deps.length > 0}
		<Card.Root>
			<Card.Header class="pb-3">
				<Card.Title class="text-sm font-medium flex items-center gap-2">
					{mode === 'scaffolding' ? 'Backend' : ''} Dependencies
					<Badge variant="outline" class="text-xs">{deps.length}</Badge>
				</Card.Title>
			</Card.Header>
			<Card.Content class="p-0">
				<div class="overflow-x-auto">
					<table class="w-full text-sm">
						<thead>
							<tr class="border-b bg-muted/30">
								<th class="px-4 py-2 text-left text-xs font-medium text-muted-foreground">Package</th>
								<th class="px-4 py-2 text-left text-xs font-medium text-muted-foreground">Version</th>
							</tr>
						</thead>
						<tbody class="divide-y">
							{#each deps as dep}
								{@const parts = typeof dep === 'string' ? dep.split(/[=<>~!]+/) : [String(dep)]}
								<tr class="hover:bg-muted/30">
									<td class="px-4 py-2 font-mono text-xs">{parts[0]}</td>
									<td class="px-4 py-2"><Badge variant="outline" class="text-xs">{parts[1] ?? 'latest'}</Badge></td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</Card.Content>
		</Card.Root>
	{:else}
		<Card.Root>
			<Card.Content class="py-10 text-center">
				<Package class="mx-auto h-10 w-10 text-muted-foreground/30 mb-3" />
				<p class="text-sm text-muted-foreground">No dependencies recorded</p>
			</Card.Content>
		</Card.Root>
	{/if}
</section>
