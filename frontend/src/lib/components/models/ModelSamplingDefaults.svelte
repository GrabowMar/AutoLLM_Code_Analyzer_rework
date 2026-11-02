<script lang="ts">
	/**
	 * Sampling defaults, side by side: what the model authors recommend
	 * (generation_config.json), what OpenRouter publishes as defaults, and what
	 * this platform's runs of this model actually sent (self-fetched; agent runs
	 * under `provider_default` send nothing, so the serving provider's own
	 * defaults applied — values unknown). Authors' values are "recommended",
	 * never "used".
	 */
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import SlidersHorizontal from '@lucide/svelte/icons/sliders-horizontal';
	import { getModelSamplingUsage, type SamplingUsage } from '$lib/api/client';
	import { formatApiError } from '$lib/api/core';
	import InheritedNote from './InheritedNote.svelte';
	import { shortSha, type HfGenerationConfig, type ReasoningInfo } from './helpers';

	interface Props {
		slug: string;
		/** Authors' generation_config.json (null when absent / gated / not fetched). */
		authors: HfGenerationConfig | null;
		/** Why `authors` is null: the generation_config fetch status, if known. */
		authorsStatus?: string | null;
		hasRepo: boolean;
		/** OpenRouter `default_parameters`; null when the model is not served via OpenRouter. */
		openrouterDefaults: Record<string, unknown> | null;
		reasoning: ReasoningInfo | null;
		inheritedFrom?: string | null;
		inheritedSlug?: string | null;
	}

	let {
		slug,
		authors,
		authorsStatus = null,
		hasRepo,
		openrouterDefaults,
		reasoning,
		inheritedFrom = null,
		inheritedSlug = null,
	}: Props = $props();

	const KEYS = ['temperature', 'top_p', 'top_k', 'min_p', 'repetition_penalty', 'frequency_penalty', 'presence_penalty'] as const;

	const POLICY_LABELS: Record<string, string> = {
		provider_default: 'provider defaults (harness sampling stripped)',
		harness_native: 'harness-native sampling',
		template_mode: 'template mode (platform sends values)',
		not_recorded: 'not recorded (older run)',
	};

	const UNKNOWN_TITLE =
		'Not forced by the platform, but the harness may have sent its own value (harness_native pass-through, or an older run that recorded no sampling policy)';

	let usage = $state<SamplingUsage | null>(null);
	let loading = $state(true);
	let error = $state('');

	async function load() {
		loading = true;
		error = '';
		try {
			usage = await getModelSamplingUsage(slug);
		} catch (e) {
			error = formatApiError(e, 'Could not load the sampling of your runs.');
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		if (slug) load();
	});

	function fmt(v: unknown): string {
		if (v === null || v === undefined) return '—';
		if (typeof v === 'object') return JSON.stringify(v);
		return String(v);
	}

	function authorValue(key: string): string {
		if (!authors) return '—';
		return fmt((authors as unknown as Record<string, unknown>)[key]);
	}

	const authorsCaption = $derived.by(() => {
		if (authors) return `generation_config.json${authors.commit ? ` @ ${shortSha(authors.commit)}` : ''}`;
		if (!hasRepo) return 'no Hugging Face repo';
		if (authorsStatus === 'gated_inaccessible') return 'gated (license not accepted)';
		if (authorsStatus === 'not_found') return 'repo ships no generation_config.json';
		return 'not fetched yet';
	});

	const reasoningDefault = $derived.by(() => {
		if (!reasoning) return '—';
		const parts: string[] = [];
		if (reasoning.mandatory) parts.push('always on');
		else if (reasoning.default_enabled != null) parts.push(reasoning.default_enabled ? 'on by default' : 'off by default');
		if (reasoning.default_effort) parts.push(`effort ${reasoning.default_effort}`);
		return parts.join(' · ') || '—';
	});

	const reasoningSent = $derived(usage?.params.find((p) => p.key === 'reasoning') ?? null);
</script>

<Card.Root class="gap-0 overflow-clip py-0">
	<Card.Header class="border-b border-border py-3">
		<Card.Title class="flex items-center gap-2 text-sm font-medium">
			<SlidersHorizontal class="size-4 text-muted-foreground" aria-hidden="true" />Sampling defaults
		</Card.Title>
		<Card.Description class="text-xs">
			Recommended by the model authors vs OpenRouter's published defaults vs what your runs of this model sent. Recommended values are not what any provider applied.
		</Card.Description>
		<InheritedNote from={inheritedFrom} slug={inheritedSlug} />
	</Card.Header>
	<Card.Content class="p-0">
		<div class="overflow-x-auto">
			<table>
				<thead>
					<tr>
						<th>Parameter</th>
						<th class="num">Authors · recommended<span class="cell-sub block text-[11px] font-normal normal-case text-muted-foreground">{authorsCaption}</span></th>
						<th class="num">OpenRouter default<span class="cell-sub block text-[11px] font-normal normal-case text-muted-foreground">{openrouterDefaults ? 'default_parameters' : 'not served via OpenRouter'}</span></th>
						<th>Your runs · sent<span class="cell-sub block text-[11px] font-normal normal-case text-muted-foreground">{usage ? `n=${usage.n_jobs} runs` : ''}</span></th>
					</tr>
				</thead>
				<tbody>
					{#each KEYS as key (key)}
						{@const sent = usage?.params.find((p) => p.key === key)}
						<tr>
							<td class="font-mono text-xs">{key}</td>
							<td class="num">{authorValue(key)}</td>
							<td class="num">{openrouterDefaults ? fmt(openrouterDefaults[key]) : '—'}</td>
							<td class="text-xs">
								{#if loading && !usage}
									<span class="text-muted-foreground">…</span>
								{:else if !usage || usage.n_jobs === 0}
									<span class="text-muted-foreground">—</span>
								{:else}
									{#each sent?.values ?? [] as v (JSON.stringify(v.value))}
										<span class="mr-2 font-mono">{fmt(v.value)} <span class="text-muted-foreground">×{v.n}</span></span>
									{/each}
									{#if sent && sent.n_not_sent > 0}
										<span class="mr-2 text-muted-foreground">not sent ×{sent.n_not_sent}</span>
									{/if}
									{#if sent?.n_unknown}
										<span class="text-muted-foreground" title={UNKNOWN_TITLE}>unknown ×{sent.n_unknown}</span>
									{/if}
								{/if}
							</td>
						</tr>
					{/each}
					<tr>
						<td class="font-mono text-xs">reasoning</td>
						<td class="num text-muted-foreground">—</td>
						<td class="num text-xs">{openrouterDefaults || reasoning ? reasoningDefault : '—'}</td>
						<td class="text-xs">
							{#if usage && usage.n_jobs > 0 && reasoningSent}
								{#each reasoningSent.values as v (JSON.stringify(v.value))}
									<span class="mr-2 font-mono">{fmt(v.value)} <span class="text-muted-foreground">×{v.n}</span></span>
								{/each}
								{#if reasoningSent.n_not_sent > 0}<span class="mr-2 text-muted-foreground">not sent ×{reasoningSent.n_not_sent}</span>{/if}
								{#if reasoningSent.n_unknown}<span class="text-muted-foreground" title={UNKNOWN_TITLE}>unknown ×{reasoningSent.n_unknown}</span>{/if}
							{:else}
								<span class="text-muted-foreground">—</span>
							{/if}
						</td>
					</tr>
				</tbody>
			</table>
		</div>
		<div class="space-y-2 border-t border-border px-4 py-3 text-xs">
			{#if authors?.from_model_config}
				<p class="text-muted-foreground">The authors' file was written by transformers from the model config (<span class="font-mono">_from_model_config</span>): its values may be library boilerplate rather than tuned recommendations.</p>
			{/if}
			{#if error}
				<ErrorAlert message={error} onretry={load} />
			{:else if loading && !usage}
				<LoadingState label="loading your runs" class="py-3" />
			{:else if usage && usage.n_jobs > 0}
				<p class="flex flex-wrap items-center gap-1.5">
					<span class="text-muted-foreground">Sampling policy of your {usage.n_jobs} runs:</span>
					{#each Object.entries(usage.policies) as [policy, count] (policy)}
						<Badge variant="outline" title={POLICY_LABELS[policy] ?? policy}>{policy} ×{count}</Badge>
					{/each}
				</p>
				{#if usage.policies.provider_default}
					<p class="text-muted-foreground">
						Under <span class="font-mono">provider_default</span> the gateway forwards no sampling params: each call sampled at the serving provider's own defaults, which are not reported (possibly different per provider).
					</p>
				{/if}
				{#if usage.harness_sampling_stripped.length}
					<p class="text-muted-foreground">
						Stripped from harness requests:
						{#each usage.harness_sampling_stripped as s (s.key)}
							<span class="mr-2"><span class="font-mono text-foreground">{s.key}</span> in {s.jobs} {s.jobs === 1 ? 'run' : 'runs'} ({s.calls} calls)</span>
						{/each}
					</p>
				{/if}
				{#if usage.presets.length}
					<p class="text-muted-foreground">
						Sampling presets:
						{#each usage.presets as p (p.id)}
							<span class="mr-2"><span class="text-foreground">{p.name}</span> v{p.version} ×{p.n}</span>
						{/each}
					</p>
				{/if}
			{:else if usage}
				<p class="text-muted-foreground">You have not run this model yet; nothing was sent.</p>
			{/if}
		</div>
	</Card.Content>
</Card.Root>
