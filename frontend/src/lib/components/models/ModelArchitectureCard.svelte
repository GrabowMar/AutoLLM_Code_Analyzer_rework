<script lang="ts">
	/**
	 * Architecture + parameters of the model's Hugging Face repo: dense vs MoE,
	 * layers, widths, attention heads / KV heads, experts total and active per
	 * token, native vs extended context, native dtype / quantization (parsed from
	 * config.json), checkpoint vs reported vs active parameters (each with its
	 * source) and the weight files. Everything missing shows "—"; estimates say so.
	 */
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import Cpu from '@lucide/svelte/icons/cpu';
	import InheritedNote from './InheritedNote.svelte';
	import {
		PARAM_SOURCE_LABELS,
		fmtBytes,
		fmtParams,
		formatNumber,
		shortSha,
		type HuggingFaceInfo,
		type ModelParams,
	} from './helpers';

	interface Props {
		hf: HuggingFaceInfo | null;
		params: ModelParams | null;
		/** Repo the catalog names (`hugging_face_id`), even when no Hub record is stored. */
		hfId?: string | null;
		inheritedFrom?: string | null;
		inheritedSlug?: string | null;
	}

	let { hf, params, hfId = null, inheritedFrom = null, inheritedSlug = null }: Props = $props();

	const arch = $derived(hf?.architecture ?? null);
	const files = $derived(hf?.files ?? null);
	const configStatus = $derived(hf?.fetch_status?.config ?? null);

	function n(v: number | null | undefined): string {
		return v == null ? '—' : formatNumber(v);
	}

	interface Row {
		label: string;
		value: string;
		title?: string;
	}

	const rows = $derived.by<Row[]>(() => {
		if (!arch) return [];
		const out: Row[] = [];
		const moe = arch.moe;
		// "Dense" only for a config we actually read (layers + width, no unparsed expert keys);
		// e.g. moondream3's config.json carries none of that, so its type is unknown.
		const readable = !!arch.num_hidden_layers && !!arch.hidden_size && !arch.has_expert_keys;
		out.push({
			label: 'Type',
			value: arch.is_moe ? 'Mixture of experts' : readable ? 'Dense' : '—',
			title: arch.is_moe || readable ? arch.architectures.join(', ') : 'config.json layout not recognised: dense vs MoE unknown',
		});
		out.push({ label: 'model_type', value: [arch.model_type, arch.text_model_type].filter(Boolean).join(' / ') || '—' });
		out.push({ label: 'Layers', value: n(arch.num_hidden_layers) });
		out.push({ label: 'Hidden size', value: n(arch.hidden_size) });
		const heads = arch.num_attention_heads;
		const kv = arch.num_key_value_heads;
		const ratio = heads && kv && kv < heads ? ` (${Math.round(heads / kv)}:1)` : '';
		out.push({
			label: 'Heads / KV heads',
			value: heads == null && kv == null ? '—' : `${n(heads)} / ${n(kv)}${ratio}`,
			title: 'Query heads / key-value heads; the ratio is the grouped-query sharing factor',
		});
		const att = arch.attention;
		if (att?.variant) {
			const extra = [att.kv_lora_rank ? `kv rank ${att.kv_lora_rank}` : '', att.q_lora_rank ? `q rank ${att.q_lora_rank}` : '']
				.filter(Boolean)
				.join(', ');
			out.push({
				label: 'Attention',
				value: `${att.variant}${extra ? ` · ${extra}` : ''}`,
				title: 'Derived from config.json (MLA = latent KV; GQA/MQA = shared KV heads)',
			});
		}
		if (att?.layer_types) {
			out.push({
				label: 'Layer types',
				value: Object.entries(att.layer_types)
					.map(([k, c]) => `${c} ${k.replaceAll('_', ' ')}`)
					.join(' · '),
			});
		}
		if (att?.sliding_window) out.push({ label: 'Sliding window', value: `${n(att.sliding_window)} tokens` });
		if (arch.head_dim) out.push({ label: 'Head dim', value: n(arch.head_dim) });
		if (moe) {
			out.push({
				label: 'Experts / token',
				value: `${n(moe.experts_per_token)} of ${n(moe.num_experts)}${moe.num_shared_experts ? ` + ${moe.num_shared_experts} shared` : ''}`,
				title: 'Routed experts active per token, of the routed experts per MoE layer',
			});
			out.push({ label: 'Expert FFN', value: n(moe.moe_intermediate_size) });
			if (moe.first_k_dense_replace) out.push({ label: 'Dense first layers', value: n(moe.first_k_dense_replace) });
		} else if (arch.intermediate_size) {
			out.push({ label: 'FFN size', value: n(arch.intermediate_size) });
		}
		if (moe?.num_nextn_predict_layers) out.push({ label: 'MTP layers', value: n(moe.num_nextn_predict_layers), title: 'Multi-token-prediction heads (speculative decoding)' });
		const rope = arch.rope;
		const extended = rope?.scaling_type && rope.original_max_position_embeddings;
		out.push({
			label: 'Context (config)',
			value: extended
				? `${n(rope.original_max_position_embeddings)} native → ${n(arch.max_position_embeddings)} (${rope.scaling_type}${rope.factor ? ` ×${rope.factor}` : ''})`
				: n(arch.max_position_embeddings),
			title: 'max_position_embeddings; rope scaling extends the trained context',
		});
		out.push({ label: 'Vocabulary', value: n(arch.vocab_size) });
		const q = arch.quantization_config;
		out.push({
			label: 'Native precision',
			value: [
				arch.torch_dtype ?? '',
				q?.quant_method ? `${q.quant_method}${q.fmt ? ` ${q.fmt}` : ''}${q.bits ? ` ${q.bits}-bit` : ''}` : '',
				q?.expert_dtype ? `experts ${q.expert_dtype}` : '',
			]
				.filter(Boolean)
				.join(' · ') || '—',
			title: q?.weight_block_size ? `Block size ${q.weight_block_size.join('×')}` : 'torch_dtype and quantization_config of the release',
		});
		if (arch.tie_word_embeddings != null) out.push({ label: 'Tied embeddings', value: arch.tie_word_embeddings ? 'Yes' : 'No' });
		return out;
	});

	const bytesPerParam = $derived(
		files?.weights_bytes && params?.checkpoint_total ? files.weights_bytes / params.checkpoint_total : null
	);
</script>

<Card.Root>
	<Card.Header class="pb-0">
		<Card.Title class="flex items-center gap-2 text-sm font-medium"><Cpu class="size-4 text-muted-foreground" aria-hidden="true" />Architecture &amp; size</Card.Title>
		<Card.Description class="text-xs">
			{#if hf}
				Hugging Face repo <span class="font-mono">{hf.id}</span>{hf.revision ? ` @ ${shortSha(hf.revision)}` : ''} (config.json, file tree) · fetched {hf.fetched_at.slice(0, 10)}
			{:else}
				Hugging Face repo config
			{/if}
		</Card.Description>
		<InheritedNote from={inheritedFrom} slug={inheritedSlug} />
	</Card.Header>
	<Card.Content class="space-y-4">
		{#if !hf && hfId}
			<EmptyState compact icon={Cpu} title="No Hugging Face record" description="The catalog names {hfId}, but no Hub record is stored (the repo was not found or has not been fetched yet), so its architecture is unknown." />
		{:else if !hf}
			<EmptyState compact icon={Cpu} title="No Hugging Face repo linked" description="Neither OpenRouter nor a catalog twin names a repo for this model, so its architecture is unknown." />
		{:else}
			{#if arch}
				<dl class="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
					{#each rows as row (row.label)}
						<div class="grid min-w-0 grid-cols-[8.5rem_minmax(0,1fr)] gap-2" title={row.title}>
							<dt class="text-muted-foreground">{row.label}</dt>
							<dd class="min-w-0 font-mono text-xs leading-5 break-words">{row.value}</dd>
						</div>
					{/each}
				</dl>
			{:else}
				<p class="text-sm text-muted-foreground">
					{#if configStatus === 'gated_inaccessible'}
						config.json is gated: the fetching account has not accepted this repo's license, so the architecture is unavailable.
					{:else if hf.fetch_status}
						No readable config.json in the repo.
					{:else}
						Not fetched yet — refresh the model metadata to read config.json.
					{/if}
				</p>
			{/if}

			<div>
				<div class="section-label mb-2">Parameters</div>
				<dl class="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-3">
					<div title={params?.checkpoint_note ?? 'safetensors element count of the repo'}>
						<dt class="kpi-label">Checkpoint</dt>
						<dd class="font-mono text-lg font-medium tabular-nums">{fmtParams(params?.checkpoint_total ?? hf.parameters)}</dd>
						<dd class="text-[11px] text-muted-foreground">all stored tensors</dd>
					</div>
					<div>
						<dt class="kpi-label">Reported total</dt>
						<dd class="font-mono text-lg font-medium tabular-nums">{fmtParams(params?.reported_total)}</dd>
						<dd class="text-[11px] text-muted-foreground">{params?.reported_total ? (params.active_source === 'name_pattern' ? 'from the repo name' : 'model card') : 'not published'}</dd>
					</div>
					<div title={params?.active_note ?? ''}>
						<dt class="kpi-label">Active / token</dt>
						<dd class="font-mono text-lg font-medium tabular-nums">
							{params?.active != null ? `${params.active_source === 'config_estimate' ? '~' : ''}${fmtParams(params.active)}` : '—'}
						</dd>
						<dd class="flex flex-wrap items-center gap-1 text-[11px] text-muted-foreground">
							{#if params?.active_source}
								<Badge variant="outline" class="h-5 px-1.5">{PARAM_SOURCE_LABELS[params.active_source] ?? params.active_source}</Badge>
							{:else}
								unknown
							{/if}
						</dd>
					</div>
				</dl>
				{#if params?.active_note}<p class="mt-2 text-[11px] text-muted-foreground">{params.active_note}</p>{/if}
				{#if params?.config_estimate != null && params.active_source !== 'config_estimate'}
					<p class="mt-1 text-[11px] text-muted-foreground">
						Config estimate (cross-check, not exact): <span class="font-mono text-foreground">~{fmtParams(params.config_estimate)}</span> active.
					</p>
				{/if}
				{#if params?.mapping_note}<p class="mt-1 text-[11px] text-warning-fg">{params.mapping_note}</p>{/if}
			</div>

			{#if files}
				<div>
					<div class="section-label mb-2">Weights</div>
					<p class="font-mono text-xs">
						{fmtBytes(files.weights_bytes)}
						{files.weight_format ?? ''}
						· {files.shard_count ?? files.weight_files} {(files.shard_count ?? files.weight_files) === 1 ? 'file' : 'shards'}
						{#if bytesPerParam != null}· ~{bytesPerParam.toFixed(2)} B/param{/if}
						{#if files.other_weight_formats.length}· also {files.other_weight_formats.join(', ')}{/if}
					</p>
				</div>
			{/if}
		{/if}
	</Card.Content>
</Card.Root>
