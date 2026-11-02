<script lang="ts">
	// Per-call telemetry of a GenerationArtifact (contract C1), read defensively:
	// renders nothing when the artifact carries none.
	import { fmtInt, fmtNum, fmtSeconds, readTelemetry } from './format';

	interface Props {
		telemetry: unknown;
	}

	let { telemetry }: Props = $props();

	const view = $derived(readTelemetry(telemetry));
</script>

{#if view}
	<div class="space-y-1">
		<p class="section-label">Call telemetry</p>
		<dl class="grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-4">
			<div><dt class="text-[11px] text-muted-foreground">Duration</dt><dd class="font-mono tabular-nums">{fmtSeconds(view.duration_s)}</dd></div>
			<div><dt class="text-[11px] text-muted-foreground">Provider</dt><dd class="truncate font-mono">{view.provider ?? '—'}</dd></div>
			<div title="Time to first token as OpenRouter reports it (latency)"><dt class="text-[11px] text-muted-foreground">TTFT</dt><dd class="font-mono tabular-nums">{view.ttft_ms != null ? `${fmtInt(view.ttft_ms)} ms` : '—'}</dd></div>
			<div><dt class="text-[11px] text-muted-foreground">Decode</dt><dd class="font-mono tabular-nums">{view.decode_tps != null ? `${fmtNum(view.decode_tps)} tok/s` : '—'}</dd></div>
			<div><dt class="text-[11px] text-muted-foreground">Cached tokens</dt><dd class="font-mono tabular-nums">{fmtInt(view.cached)}</dd></div>
			<div><dt class="text-[11px] text-muted-foreground">Reasoning tokens</dt><dd class="font-mono tabular-nums">{fmtInt(view.reasoning)}</dd></div>
			<div><dt class="text-[11px] text-muted-foreground">Gateway retries</dt><dd class="font-mono tabular-nums">{fmtInt(view.retries)}</dd></div>
			<div><dt class="text-[11px] text-muted-foreground">Finish</dt><dd class="font-mono">{view.finish_reason ?? '—'}</dd></div>
		</dl>
		{#if view.served_model || view.list_cost != null || view.openrouter_status}
			<p class="text-[11px] text-muted-foreground">
				{#if view.served_model}served model <span class="font-mono">{view.served_model}</span>{/if}
				{#if view.list_cost != null} · list-price equivalent ${view.list_cost.toFixed(5)} (not billed){/if}
				{#if view.openrouter_status && view.openrouter_status !== 'ok'} · OpenRouter stats {view.openrouter_status}{/if}
			</p>
		{/if}
	</div>
{/if}
