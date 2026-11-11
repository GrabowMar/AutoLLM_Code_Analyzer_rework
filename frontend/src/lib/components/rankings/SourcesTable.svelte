<script lang="ts">
	/**
	 * The MSS input sources with their provenance: name (linked), license, as_of,
	 * fetched_at, window, and source-specific extras (plan, credit). A stale or
	 * missing as_of is shown as such, never hidden.
	 *
	 * Props: `sources` (SourceInfo[]), `class`.
	 */
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import type { SourceInfo } from '$lib/api/rankings';
	import { cn } from '$lib/utils';
	import { fmtDate, fmtUsd } from './mss';

	interface Props {
		sources: SourceInfo[];
		class?: string;
	}

	let { sources, class: className }: Props = $props();

	/** Source-specific facts worth a line: plan, credit and how it was obtained, publish dates. */
	function extras(s: SourceInfo): string {
		const out: string[] = [];
		if (typeof s.plan === 'string') out.push(s.plan);
		if (typeof s.value_usd === 'number') out.push(fmtUsd(s.value_usd, 2));
		if (typeof s.source === 'string') out.push(s.source);
		if (Array.isArray(s.publish_dates) && s.publish_dates.length) out.push(`published ${fmtDate(s.publish_dates as string[])}`);
		return out.join(' · ');
	}
</script>

{#if sources.length === 0}
	<p class="text-xs text-muted-foreground">No sources recorded.</p>
{:else}
	<div class={cn('overflow-x-auto', className)}>
		<table class="w-full">
			<thead>
				<tr>
					<th>Source</th>
					<th>License</th>
					<th title="The date the source's data describes">As of</th>
					<th title="When this platform fetched it">Fetched</th>
					<th>Window</th>
				</tr>
			</thead>
			<tbody>
				{#each sources as s (s.name)}
					{@const extra = extras(s)}
					<tr>
						<td class="max-w-[26rem]">
							{#if s.url}
								<a href={s.url} target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1 text-xs hover:text-primary">
									<span class="truncate" title={s.name}>{s.name}</span><ExternalLink class="size-3 shrink-0" aria-hidden="true" />
								</a>
							{:else}
								<span class="text-xs">{s.name}</span>
							{/if}
							{#if extra || s.citation}
								<span class="cell-sub block truncate text-[11px] text-muted-foreground" title={s.citation ?? extra}>{extra || s.citation}</span>
							{/if}
						</td>
						<td class="text-xs text-muted-foreground">{s.license ?? '—'}</td>
						<td class="font-mono text-xs tabular-nums {s.as_of ? '' : 'text-muted-foreground'}">{s.as_of ? fmtDate(s.as_of) : 'not fetched'}</td>
						<td class="font-mono text-xs tabular-nums text-muted-foreground">{fmtDate(s.fetched_at)}</td>
						<td class="font-mono text-xs tabular-nums text-muted-foreground">
							{#if s.window}{s.window.start} – {s.window.end} <span class="opacity-70">({s.window.days} d)</span>{:else}—{/if}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}
