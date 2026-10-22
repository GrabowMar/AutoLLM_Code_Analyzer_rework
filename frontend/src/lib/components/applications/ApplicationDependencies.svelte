<script lang="ts">
import * as Card from '$lib/components/ui/card';
import { Badge } from '$lib/components/ui/badge';
import Package from '@lucide/svelte/icons/package';
import EmptyState from '$lib/components/EmptyState.svelte';

interface Props {
	deps: (string | unknown)[];
	mode: string;
}

let { deps, mode }: Props = $props();
</script>

<section id="dependencies" class="space-y-4">
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
							<tr>
								<th>Package</th>
								<th>Version</th>
							</tr>
						</thead>
						<tbody>
							{#each deps as dep}
								{@const parts = typeof dep === 'string' ? dep.split(/[=<>~!]+/) : [String(dep)]}
								<tr>
									<td class="font-mono text-xs">{parts[0]}</td>
									<td><Badge variant="outline" class="text-xs">{parts[1] ?? 'latest'}</Badge></td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</Card.Content>
		</Card.Root>
	{:else}
		<Card.Root>
			<Card.Content>
				<EmptyState compact icon={Package} title="No dependencies recorded" description="The generator did not report any packages for this app." />
			</Card.Content>
		</Card.Root>
	{/if}
</section>
