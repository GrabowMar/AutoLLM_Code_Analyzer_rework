<script lang="ts">
	/**
	 * Generation data tab: what the harness, the gateway and repeated runs
	 * recorded for this model on this platform — agent behaviour (trajectory
	 * summaries), provider / call telemetry and the spread between runs with
	 * the same setup. Each panel fetches its own data (keyed by the model's
	 * numeric id) and is descriptive only: n is shown, no p-values.
	 */
	import SectionHeading from '$lib/components/SectionHeading.svelte';
	import ModelBehaviourPanel from './ModelBehaviourPanel.svelte';
	import ModelProviderPerformance from './ModelProviderPerformance.svelte';
	import ModelVariabilityPanel from './ModelVariabilityPanel.svelte';

	interface Props {
		/** LLMModel primary key. */
		modelId: number;
		/** Catalog twin of a local model: its data is NOT shown here (no pooling), only named. */
		twinModelId?: string | null;
		twinSlug?: string | null;
	}

	let { modelId, twinModelId = null, twinSlug = null }: Props = $props();
</script>

<div class="space-y-8">
	{#if twinModelId}
		<p class="text-xs text-muted-foreground">
			Measurements below are this endpoint model's own runs only. Runs of its catalog twin
			{#if twinSlug}<a href="/models/{twinSlug}" class="font-mono text-primary hover:underline">{twinModelId}</a>{:else}<span class="font-mono">{twinModelId}</span>{/if}
			are not pooled in.
		</p>
	{/if}

	<section class="space-y-3" aria-labelledby="gen-behaviour">
		<SectionHeading id="gen-behaviour" size="sm" title="Agent behaviour" lead="What the coding harness did in agent runs: actions, builds, tests, servers, context growth (trajectory summaries)." />
		<ModelBehaviourPanel {modelId} />
	</section>

	<section class="space-y-3" aria-labelledby="gen-providers">
		<SectionHeading id="gen-providers" size="sm" title="Providers and calls" lead="Per-call telemetry from the gateway: which provider served the calls, latency, retries, finish reasons, billed vs list cost." />
		<ModelProviderPerformance {modelId} />
	</section>

	<section class="space-y-3" aria-labelledby="gen-variability">
		<SectionHeading id="gen-variability" size="sm" title="Same-setup variability" lead="Spread between runs that share a setup (config fingerprint): how much outcomes move when nothing but sampling changes." />
		<ModelVariabilityPanel {modelId} />
	</section>
</div>
