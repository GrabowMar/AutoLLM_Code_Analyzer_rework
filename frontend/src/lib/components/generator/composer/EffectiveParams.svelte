<script lang="ts">
	/**
	 * EffectiveParams — the sampling params a run will send, each with the layer
	 * it came from ("temperature 0.3 · mode default").
	 *
	 * Props: `effective`, `sources` (key → source token), `emptyText`.
	 */
	import type { LLMParams } from '$lib/api/generation';
	import { LLM_PARAM_KEYS, formatLLMValue, llmSourceLabel } from '../llm-params';

	interface Props {
		effective: LLMParams;
		sources?: Record<string, string>;
		emptyText?: string;
	}

	let { effective, sources = {}, emptyText = 'Provider defaults (nothing set explicitly)' }: Props = $props();

	const rows = $derived.by(() => {
		const keys = Object.keys(effective).filter((k) => effective[k as keyof LLMParams] != null);
		const ordered = [
			...LLM_PARAM_KEYS.filter((k) => keys.includes(k)),
			...keys.filter((k) => !(LLM_PARAM_KEYS as string[]).includes(k)).sort(),
		];
		return ordered.map((key) => ({
			key,
			value: formatLLMValue(key, effective[key as keyof LLMParams]),
			source: llmSourceLabel(sources[key]),
		}));
	});
</script>

{#if rows.length === 0}
	<p class="text-xs text-muted-foreground">{emptyText}</p>
{:else}
	<dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
		{#each rows as row (row.key)}
			<dt class="font-mono text-muted-foreground">{row.key}</dt>
			<dd class="min-w-0 break-words">
				<span class="font-mono text-foreground">{row.value}</span>
				{#if row.source}<span class="text-muted-foreground"> · {row.source}</span>{/if}
			</dd>
		{/each}
	</dl>
{/if}
