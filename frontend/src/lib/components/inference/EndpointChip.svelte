<script lang="ts">
	/**
	 * EndpointChip — one endpoint in a compact row: `gpu-box-1 ● 2/2 req · qwen2.5-coder:32b +2`.
	 *
	 * Props:
	 * - `endpoint`: `{slug, name, kind, online}` (any endpoint ref: EndpointSummary,
	 *   EndpointCapacity, LLMModelSummary.endpoint, ExperimentEndpointRef).
	 * - `status`: full status when known ('degraded' / 'disabled' refine `online`).
	 * - `slots`: in-flight requests / max_concurrency.
	 * - `models`: the viewer's own loaded / resident model names (first one shown).
	 * - `othersCount`: other tenants' resident models, shown only as "+N".
	 * - `href`: render as a link (e.g. `/endpoints/{id}`).
	 * - `class`.
	 */
	import { cn } from '$lib/utils';
	import { toneClasses } from '$lib/constants/colors';
	import type { EndpointStatus } from '$lib/api/types/inference';
	import { endpointStatusTone, kindLabel } from './endpoint-display';

	interface Props {
		endpoint: { slug: string; name: string; kind: string; online: boolean };
		status?: EndpointStatus | null;
		slots?: { used: number; total: number } | null;
		models?: string[];
		othersCount?: number;
		href?: string;
		class?: string;
	}

	let { endpoint, status, slots, models = [], othersCount = 0, href, class: className }: Props = $props();

	const resolved = $derived<EndpointStatus>(status ?? (endpoint.online ? 'online' : 'offline'));
	const tone = $derived(endpointStatusTone(resolved));
	const extraModels = $derived(Math.max(0, models.length - 1) + Math.max(0, othersCount));
	const title = $derived(
		[
			`${endpoint.name || endpoint.slug} (${kindLabel(endpoint.kind)}) — ${resolved}`,
			slots ? `${slots.used} of ${slots.total} request slots busy` : '',
			models.length > 0 ? `Loaded: ${models.join(', ')}` : '',
			othersCount > 0 ? `${othersCount} more loaded by other users` : '',
		]
			.filter(Boolean)
			.join('\n')
	);
	const classes = $derived(
		cn(
			'inline-flex max-w-full min-w-0 items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium leading-4 tabular-nums',
			toneClasses[tone],
			href &&
				'cursor-pointer transition-colors duration-150 hover:border-current focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40',
			className
		)
	);
</script>

{#snippet body()}
	<span class="truncate font-mono">{endpoint.slug}</span>
	<span class={cn('status-dot h-1.5 w-1.5 shrink-0', resolved === 'online' && 'status-dot-live')} aria-hidden="true"></span>
	<span class="sr-only">{resolved}</span>
	{#if slots}
		<span class="shrink-0">{slots.used}/{slots.total} req</span>
	{/if}
	{#if models.length > 0}
		<span class="shrink-0 opacity-60" aria-hidden="true">·</span>
		<span class="min-w-0 truncate font-mono">{models[0]}</span>
	{/if}
	{#if extraModels > 0}
		<span class="shrink-0">+{extraModels}</span>
	{/if}
{/snippet}

{#if href}
	<a {href} class={classes} {title}>{@render body()}</a>
{:else}
	<span class={classes} {title}>{@render body()}</span>
{/if}
