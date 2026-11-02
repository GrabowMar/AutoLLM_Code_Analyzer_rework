<script lang="ts">
	/**
	 * HttpExchangeList — requests participants sent through the HTTP console,
	 * newest first: one summary line each (time, method, path, status or
	 * error, elapsed, who / which app), expanding to request and response
	 * headers and body excerpts. Every value is shown as plain text.
	 *
	 * Props: `exchanges`, `showWho` (participant + app columns for task-wide lists).
	 */
	import type { HttpExchangeRecord } from '$lib/api/human-eval';
	import { formatDateTime } from '$lib/utils/formatters';
	import { cn } from '$lib/utils';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';

	interface Props {
		exchanges: HttpExchangeRecord[];
		showWho?: boolean;
	}

	let { exchanges, showWho = false }: Props = $props();

	function statusClass(code: number | null): string {
		if (code == null) return 'text-danger-fg';
		if (code >= 500) return 'text-danger-fg';
		if (code >= 400) return 'text-warning-fg';
		return 'text-foreground';
	}

	function pretty(body: string): string {
		const text = body.trim();
		if (!text || (text[0] !== '{' && text[0] !== '[')) return body;
		try {
			return JSON.stringify(JSON.parse(text), null, 2);
		} catch {
			return body;
		}
	}

	const headerEntries = (h: Record<string, string>) => Object.entries(h ?? {});
</script>

<ul class="divide-y divide-border/60 rounded-md border border-border">
	{#each exchanges as x (x.id)}
		<li>
			<details class="group">
				<summary class="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-xs hover:bg-muted/40 [&::-webkit-details-marker]:hidden">
					<ChevronRight class="size-3.5 shrink-0 text-muted-foreground transition-transform group-open:rotate-90 motion-reduce:transition-none" aria-hidden="true" />
					<span class="w-12 shrink-0 font-mono font-semibold">{x.method}</span>
					<span class="min-w-0 flex-1 truncate font-mono" title={x.path}>{x.path}</span>
					<span class={cn('shrink-0 font-mono tabular-nums', statusClass(x.status_code))}>
						{x.status_code ?? (x.error ? 'error' : '—')}
					</span>
					<span class="w-16 shrink-0 text-right font-mono tabular-nums text-muted-foreground">{x.elapsed_ms != null ? `${x.elapsed_ms} ms` : ''}</span>
					{#if showWho}
						<span class="hidden w-28 shrink-0 truncate text-muted-foreground md:inline" title="{x.participant_label} · {x.app_label}">{x.participant_label} · {x.app_label}</span>
					{/if}
					<span class="hidden w-32 shrink-0 text-right text-muted-foreground sm:inline">{formatDateTime(x.created_at)}</span>
				</summary>
				<div class="space-y-3 border-t border-border/60 bg-muted/20 px-3 py-3 text-xs">
					{#if x.error}<p class="text-danger-fg">Error: {x.error}</p>{/if}
					<div class="grid gap-3 lg:grid-cols-2">
						<section class="min-w-0 space-y-1.5">
							<p class="section-label">Request</p>
							{#if headerEntries(x.request_headers).length}
								<dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 font-mono">
									{#each headerEntries(x.request_headers) as [name, value] (name)}
										<dt class="text-muted-foreground">{name}</dt>
										<dd class="min-w-0 break-all">{value}</dd>
									{/each}
								</dl>
							{/if}
							{#if x.request_body}
								<pre class="max-h-64 overflow-auto whitespace-pre-wrap break-all rounded border border-border bg-card p-2 font-mono">{pretty(x.request_body)}</pre>
							{:else}
								<p class="text-muted-foreground">No body.</p>
							{/if}
						</section>
						<section class="min-w-0 space-y-1.5">
							<p class="section-label">Response {x.status_code ?? ''}</p>
							{#if headerEntries(x.response_headers).length}
								<dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 font-mono">
									{#each headerEntries(x.response_headers) as [name, value] (name)}
										<dt class="text-muted-foreground">{name}</dt>
										<dd class="min-w-0 break-all">{value}</dd>
									{/each}
								</dl>
							{/if}
							{#if x.response_body}
								<pre class="max-h-64 overflow-auto whitespace-pre-wrap break-all rounded border border-border bg-card p-2 font-mono">{pretty(x.response_body)}</pre>
								{#if x.response_truncated}<p class="text-muted-foreground">Truncated: only the first part was read and stored.</p>{/if}
							{:else}
								<p class="text-muted-foreground">No body.</p>
							{/if}
						</section>
					</div>
					{#if showWho}<p class="text-muted-foreground md:hidden">{x.participant_label} · {x.app_label}</p>{/if}
				</div>
			</details>
		</li>
	{/each}
</ul>
