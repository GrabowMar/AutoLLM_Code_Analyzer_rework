<script lang="ts">
import * as Card from '$lib/components/ui/card';
import { Badge } from '$lib/components/ui/badge';
import Bot from '@lucide/svelte/icons/bot';
import CircleX from '@lucide/svelte/icons/circle-x';
import type { CopilotIteration } from '$lib/api/client';

interface Props {
	iterations: CopilotIteration[];
}

let { iterations }: Props = $props();
</script>

<section id="iterations" class="space-y-4">
	<h2 class="text-lg font-semibold flex items-center gap-2"><Bot class="h-5 w-5" /> Copilot Iterations</h2>
	<div class="relative pl-6">
		<!-- Timeline line -->
		<div class="absolute left-2.5 top-0 bottom-0 w-px bg-border"></div>

		{#each iterations as it}
			<div class="relative mb-4">
				<!-- Timeline dot -->
				<div class="absolute -left-6 top-4 w-5 h-5 rounded-full border-2 flex items-center justify-center text-[10px] font-bold {it.build_success ? 'border-emerald-500 bg-emerald-500/20 text-emerald-400' : 'border-red-500 bg-red-500/20 text-red-400'}">
					{it.iteration_number}
				</div>

				<Card.Root>
					<Card.Header class="pb-2">
						<div class="flex items-center justify-between">
							<div class="flex items-center gap-2">
								<Card.Title class="text-sm font-medium">Iteration {it.iteration_number}</Card.Title>
								<Badge variant="outline" class="text-xs">{it.action}</Badge>
							</div>
							<Badge variant="outline" class="text-xs {it.build_success ? 'bg-emerald-500/15 text-emerald-500 border-emerald-500/30' : 'bg-red-500/15 text-red-400 border-red-500/30'}">
								{it.build_success ? '✓ Build OK' : '✗ Build Failed'}
							</Badge>
						</div>
					</Card.Header>
					<Card.Content class="space-y-3">
						{#if it.errors_detected && it.errors_detected.length > 0}
							<div>
								<div class="text-xs font-medium text-muted-foreground mb-1">Errors Detected ({it.errors_detected.length})</div>
								<ul class="space-y-1">
									{#each it.errors_detected as err}
										<li class="flex items-start gap-2 text-xs">
											<CircleX class="h-3.5 w-3.5 text-red-400 shrink-0 mt-0.5" />
											<span class="text-red-400/90">{err}</span>
										</li>
									{/each}
								</ul>
							</div>
						{/if}
						{#if it.fix_applied}
							<div>
								<div class="text-xs font-medium text-muted-foreground mb-1">Fix Applied</div>
								<pre class="text-xs font-mono bg-zinc-950 rounded-md p-3 max-h-32 overflow-x-auto overflow-y-auto text-zinc-300">{it.fix_applied}</pre>
							</div>
						{/if}
						{#if it.build_output}
							<div>
								<div class="text-xs font-medium text-muted-foreground mb-1">Build Output</div>
								<pre class="text-xs font-mono bg-zinc-950 rounded-md p-3 max-h-32 overflow-x-auto overflow-y-auto text-zinc-300">{it.build_output}</pre>
							</div>
						{/if}
					</Card.Content>
				</Card.Root>
			</div>
		{/each}
	</div>
</section>
