<script lang="ts">
import * as Card from '$lib/components/ui/card';
import { Badge } from '$lib/components/ui/badge';
import StatusBadge from '$lib/components/StatusBadge.svelte';
import EmptyState from '$lib/components/EmptyState.svelte';
import Bot from '@lucide/svelte/icons/bot';
import CircleX from '@lucide/svelte/icons/circle-x';
import type { CopilotIteration } from '$lib/api/client';

interface Props {
	iterations: CopilotIteration[];
}

let { iterations }: Props = $props();
</script>

<section id="iterations" class="space-y-4">
	{#if iterations.length === 0}
		<Card.Root>
			<Card.Content>
				<EmptyState compact icon={Bot} title="No iterations recorded" description="Copilot build/fix iterations appear here as they run." />
			</Card.Content>
		</Card.Root>
	{:else}
	<div class="relative pl-6">
		<!-- Timeline line -->
		<div class="absolute left-2.5 top-0 bottom-0 w-px bg-border"></div>

		{#each iterations as it}
			<div class="relative mb-4">
				<!-- Timeline dot -->
				<div class="absolute -left-6 top-4 w-5 h-5 rounded-full border-2 flex items-center justify-center text-[11px] font-bold {it.build_success ? 'border-success bg-success/15 text-success-fg' : 'border-destructive bg-destructive/15 text-danger-fg'}">
					{it.iteration_number}
				</div>

				<Card.Root>
					<Card.Header class="pb-2">
						<div class="flex items-center justify-between">
							<div class="flex items-center gap-2">
								<Card.Title class="text-sm font-medium">Iteration {it.iteration_number}</Card.Title>
								<Badge variant="outline" class="text-xs">{it.action}</Badge>
							</div>
							<StatusBadge status={null} tone={it.build_success ? 'success' : 'danger'} label={it.build_success ? 'Build OK' : 'Build failed'} />
						</div>
					</Card.Header>
					<Card.Content class="space-y-3">
						{#if it.errors_detected && it.errors_detected.length > 0}
							<div>
								<div class="section-label mb-1">Errors Detected ({it.errors_detected.length})</div>
								<ul class="space-y-1">
									{#each it.errors_detected as err}
										<li class="flex items-start gap-2 text-xs">
											<CircleX class="h-3.5 w-3.5 text-destructive shrink-0 mt-0.5" />
											<span class="text-danger-fg">{err}</span>
										</li>
									{/each}
								</ul>
							</div>
						{/if}
						{#if it.fix_applied}
							<div>
								<div class="section-label mb-1">Fix Applied</div>
								<pre class="text-xs font-mono bg-muted/40 rounded-md p-3 max-h-32 overflow-x-auto overflow-y-auto text-foreground/90">{it.fix_applied}</pre>
							</div>
						{/if}
						{#if it.build_output}
							<div>
								<div class="section-label mb-1">Build Output</div>
								<pre class="text-xs font-mono bg-muted/40 rounded-md p-3 max-h-32 overflow-x-auto overflow-y-auto text-foreground/90">{it.build_output}</pre>
							</div>
						{/if}
					</Card.Content>
				</Card.Root>
			</div>
		{/each}
	</div>
	{/if}
</section>
