<script lang="ts">
import * as Card from '$lib/components/ui/card';
import { Badge } from '$lib/components/ui/badge';
import Database from '@lucide/svelte/icons/database';
import ChevronDown from '@lucide/svelte/icons/chevron-down';
import ChevronRight from '@lucide/svelte/icons/chevron-right';
import type { GenerationArtifact } from '$lib/api/client';
import { fmt, fmtCost, fmtDate } from './utils';

interface Props {
	artifacts: GenerationArtifact[];
}

let { artifacts }: Props = $props();

let expandedArtifacts = $state<Set<number>>(new Set());

function toggleArtifact(id: number) {
	const next = new Set(expandedArtifacts);
	if (next.has(id)) next.delete(id);
	else next.add(id);
	expandedArtifacts = next;
}
</script>

<section id="artifacts" class="space-y-4">
	<h2 class="text-lg font-semibold flex items-center gap-2"><Database class="h-5 w-5" /> Artifacts</h2>
	{#if artifacts.length === 0}
		<Card.Root>
			<Card.Content class="py-10 text-center">
				<Database class="mx-auto h-10 w-10 text-muted-foreground/30 mb-3" />
				<p class="text-sm text-muted-foreground">No artifacts recorded</p>
			</Card.Content>
		</Card.Root>
	{:else}
		<Card.Root>
			<Card.Content class="p-0">
				<div class="overflow-x-auto">
					<table class="w-full text-sm">
						<thead>
							<tr class="border-b bg-muted/30">
								<th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground w-8"></th>
								<th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Stage</th>
								<th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Model</th>
								<th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Prompt</th>
								<th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Completion</th>
								<th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Cost</th>
								<th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Created</th>
							</tr>
						</thead>
						<tbody class="divide-y">
							{#each artifacts as art}
								<tr class="hover:bg-muted/30 cursor-pointer" onclick={() => toggleArtifact(art.id)}>
									<td class="px-4 py-3">
										{#if expandedArtifacts.has(art.id)}
											<ChevronDown class="h-4 w-4 text-muted-foreground" />
										{:else}
											<ChevronRight class="h-4 w-4 text-muted-foreground" />
										{/if}
									</td>
									<td class="px-4 py-3"><Badge variant="outline" class="text-xs">{art.stage}</Badge></td>
									<td class="px-4 py-3 font-mono text-xs">{art.request_payload?.model ?? art.response_payload?.model ?? '—'}</td>
									<td class="px-4 py-3">{fmt(art.prompt_tokens, 0)}</td>
									<td class="px-4 py-3">{fmt(art.completion_tokens, 0)}</td>
									<td class="px-4 py-3">{fmtCost(art.total_cost)}</td>
									<td class="px-4 py-3 text-muted-foreground">{fmtDate(art.created_at)}</td>
								</tr>
								{#if expandedArtifacts.has(art.id)}
									<tr>
										<td colspan="7" class="px-4 py-4 bg-muted/20">
											<!-- Token bar -->
											{#if (art.prompt_tokens ?? 0) + (art.completion_tokens ?? 0) > 0}
												{@const total = (art.prompt_tokens ?? 0) + (art.completion_tokens ?? 0)}
												<div class="mb-4">
													<div class="text-xs text-muted-foreground mb-1">Token Breakdown ({total} total)</div>
													<div class="h-3 rounded-full overflow-hidden bg-zinc-800 flex">
														<div class="bg-blue-500 h-full" style="width: {(art.prompt_tokens / total) * 100}%"></div>
														<div class="bg-emerald-500 h-full" style="width: {(art.completion_tokens / total) * 100}%"></div>
													</div>
													<div class="flex justify-between text-[10px] text-muted-foreground mt-0.5">
														<span>Prompt: {fmt(art.prompt_tokens, 0)}</span>
														<span>Completion: {fmt(art.completion_tokens, 0)}</span>
													</div>
												</div>
											{/if}
											<!-- Request Messages -->
											<div class="space-y-2">
												<div class="text-xs font-medium text-muted-foreground uppercase">Request Messages</div>
												{#each (art.request_payload?.messages ?? []) as msg}
													<div class="border rounded-md">
														<div class="px-3 py-1.5 border-b bg-muted/30 flex items-center gap-2">
															<Badge variant="outline" class="text-[10px] {msg.role === 'system' ? 'bg-blue-500/20 text-blue-400' : msg.role === 'user' ? 'bg-emerald-500/20 text-emerald-400' : 'bg-purple-500/20 text-purple-400'}">{msg.role}</Badge>
															<span class="text-xs text-muted-foreground">{(msg.content?.length ?? 0)} chars</span>
														</div>
														<pre class="p-3 text-xs font-mono whitespace-pre-wrap break-all max-h-48 overflow-auto">{msg.content ?? ''}</pre>
													</div>
												{/each}
											</div>
											<!-- Response -->
											{#if art.response_payload?.choices?.length > 0}
												<div class="space-y-2 mt-3">
													<div class="text-xs font-medium text-muted-foreground uppercase">Response</div>
													<div class="border rounded-md">
														<div class="px-3 py-1.5 border-b bg-muted/30 flex items-center gap-2">
															<Badge variant="outline" class="text-[10px] bg-purple-500/20 text-purple-400">assistant</Badge>
															<span class="text-xs text-muted-foreground">finish: {art.response_payload.choices[0]?.finish_reason ?? '—'}</span>
														</div>
														<pre class="p-3 text-xs font-mono whitespace-pre-wrap break-all max-h-48 overflow-auto">{art.response_payload.choices[0]?.message?.content ?? '(empty)'}</pre>
													</div>
												</div>
											{/if}
										</td>
									</tr>
								{/if}
							{/each}
						</tbody>
					</table>
				</div>
			</Card.Content>
		</Card.Root>
	{/if}
</section>
