<script lang="ts">
	/**
	 * The Hugging Face repo as an ecosystem object: adoption (downloads 30 d and
	 * all-time, likes, trending, Spaces), derived repos (finetunes, quantizations,
	 * adapters, merges), base-model relation, inference providers, papers, weight
	 * files, license and the repo revision the record was read at. Counts change
	 * over time, so the fetch date is always shown.
	 */
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import Boxes from '@lucide/svelte/icons/boxes';
	import InheritedNote from './InheritedNote.svelte';
	import { compactNumber, fmtBytes, fmtSpaces, isoDay, shortSha, type HuggingFaceInfo } from './helpers';

	interface Props {
		hf: HuggingFaceInfo;
		inheritedFrom?: string | null;
		inheritedSlug?: string | null;
	}

	let { hf, inheritedFrom = null, inheritedSlug = null }: Props = $props();

	const CHILD_LABELS = { finetune: 'finetunes', quantized: 'quantizations', adapter: 'adapters', merge: 'merges' } as const;

	const stats = $derived([
		{ label: 'Downloads 30 d', value: compactNumber(hf.downloads) },
		{ label: 'All-time', value: compactNumber(hf.downloads_all_time ?? null) },
		{ label: 'Likes', value: compactNumber(hf.likes) },
		{ label: 'Trending', value: hf.trending_score != null ? String(hf.trending_score) : '—' },
		{ label: 'Spaces', value: fmtSpaces(hf) },
	]);
	const providers = $derived(hf.inference_providers ?? []);
	const papers = $derived(hf.papers ?? []);
</script>

<Card.Root>
	<Card.Header class="pb-0">
		<Card.Title class="flex items-center gap-2 text-base">
			<Boxes class="size-4 text-muted-foreground" aria-hidden="true" />Hugging Face
			<a href="https://huggingface.co/{hf.id}" target="_blank" rel="noopener noreferrer" class="text-muted-foreground hover:text-foreground" aria-label="Open the repo on Hugging Face"><ExternalLink class="size-3.5" /></a>
		</Card.Title>
		<Card.Description class="text-xs">
			<span class="font-mono">{hf.id}</span>{hf.sha ? ` @ ${shortSha(hf.sha)}` : ''} · fetched {isoDay(hf.fetched_at) ?? '—'}{hf.authenticated ? '' : ' (anonymous)'}
		</Card.Description>
		<InheritedNote from={inheritedFrom} slug={inheritedSlug} />
	</Card.Header>
	<Card.Content class="space-y-3 text-sm">
		<dl class="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-5">
			{#each stats as s (s.label)}
				<div>
					<dt class="kpi-label">{s.label}</dt>
					<dd class="font-mono text-base font-medium tabular-nums">{s.value}</dd>
				</div>
			{/each}
		</dl>

		{#if hf.children}
			<p class="text-xs">
				<span class="text-muted-foreground">Derived repos:</span>
				{#each Object.entries(CHILD_LABELS) as [key, label] (key)}
					<span class="mr-3 font-mono">{compactNumber(hf.children[key as keyof typeof CHILD_LABELS] ?? null)} <span class="font-sans text-muted-foreground">{label}</span></span>
				{/each}
			</p>
		{/if}

		{#if hf.base_model_relation || hf.base_models.length}
			<p class="text-xs">
				<span class="text-muted-foreground">Base model:</span>
				<span class="font-mono">{(hf.base_models_expanded?.length ? hf.base_models_expanded : hf.base_models).join(', ')}</span>
				{#if hf.base_model_relation}<Badge variant="outline" class="ml-1">{hf.base_model_relation}</Badge>{/if}
			</p>
		{/if}

		<p class="text-xs">
			<span class="text-muted-foreground">License:</span>
			<span class="font-mono">{hf.license_name || hf.license || '—'}</span>
			{#if hf.gated}<Badge variant="outline" class="ml-1">gated</Badge>{/if}
			{#if hf.gated_inaccessible}<span class="ml-1 text-muted-foreground">(config files not readable with the fetching account)</span>{/if}
		</p>

		{#if hf.files}
			<p class="text-xs">
				<span class="text-muted-foreground">Weights:</span>
				<span class="font-mono">{fmtBytes(hf.files.weights_bytes)} {hf.files.weight_format ?? ''}</span>
				{#if hf.used_storage_bytes}<span class="text-muted-foreground"> · repo storage {fmtBytes(hf.used_storage_bytes)}</span>{/if}
				{#if hf.native_quant_method}<span class="text-muted-foreground"> · released as</span>&#32;<span class="font-mono">{hf.native_quant_method}</span>{/if}
			</p>
		{/if}

		{#if providers.length}
			<div>
				<div class="section-label mb-1.5">Inference providers on Hugging Face</div>
				<div class="flex flex-wrap gap-1">
					{#each providers as p (p.provider)}
						<Badge variant="outline" class="font-mono" title={p.provider_model_id ?? undefined}>{p.provider}{p.status && p.status !== 'live' ? ` · ${p.status}` : ''}</Badge>
					{/each}
				</div>
			</div>
		{/if}

		{#if papers.length}
			<div>
				<div class="section-label mb-1.5">Papers tagged on the model card</div>
				<ul class="space-y-1">
					{#each papers as p (p.id)}
						<li class="text-xs">
							<a href="https://arxiv.org/abs/{p.id}" target="_blank" rel="noopener noreferrer" class="text-primary hover:underline">{p.title ?? `arXiv:${p.id}`}</a>
							<span class="font-mono text-muted-foreground"> · {p.id}{p.published_at ? ` · ${p.published_at.slice(0, 10)}` : ''}{p.upvotes != null ? ` · ${p.upvotes} upvotes` : ''}</span>
						</li>
					{/each}
				</ul>
			</div>
		{/if}
	</Card.Content>
</Card.Root>
