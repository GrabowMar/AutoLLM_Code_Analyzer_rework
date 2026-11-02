<script lang="ts">
	/**
	 * GenerationInputs — "What shaped this generation": the frozen inputs of one
	 * run, read verbatim from its snapshot (`resolved_bundle`): app spec and
	 * stack identity, brief/prompt templates, the conformance contract, the
	 * model's context window and frozen pricing, the endpoint, harness, gateway
	 * policy, images and preset hashes, plus which weights actually served.
	 * Display only: nothing is recomputed or re-read from the live catalog.
	 *
	 * Props: `job`.
	 */
	import { Badge } from '$lib/components/ui/badge';
	import SectionHeading from '$lib/components/SectionHeading.svelte';
	import Copy from '@lucide/svelte/icons/copy';
	import type { GenerationJob } from '$lib/api/generation';
	import { copyText } from '../utils';
	import { fmtInt, isPlainObject, shortHash } from './format';

	interface Props {
		job: GenerationJob;
	}

	let { job }: Props = $props();

	interface Row {
		label: string;
		value: string;
		mono?: boolean;
		/** Full value to copy (hashes); shown truncated. */
		copy?: string;
		title?: string;
	}
	interface Group {
		title: string;
		rows: Row[];
	}

	const snap = $derived((isPlainObject(job.resolved_bundle) ? job.resolved_bundle : {}) as Record<string, any>);

	function obj(v: unknown): Record<string, any> {
		return isPlainObject(v) ? (v as Record<string, any>) : {};
	}

	function count(v: unknown): string {
		return Array.isArray(v) ? String(v.length) : '—';
	}

	function hashRow(label: string, value: unknown): Row | null {
		return typeof value === 'string' && value ? { label, value: shortHash(value), mono: true, copy: value } : null;
	}

	function row(label: string, value: unknown, opts: Partial<Row> = {}): Row | null {
		if (value == null || value === '') return null;
		const text =
			typeof value === 'boolean'
				? value
					? 'yes'
					: 'no'
				: typeof value === 'number'
					? value.toLocaleString()
					: Array.isArray(value)
						? value.join(', ')
						: isPlainObject(value)
							? JSON.stringify(value)
							: String(value);
		return { label, value: text, ...opts };
	}

	function perMillion(perToken: unknown): string | null {
		if (typeof perToken !== 'number') return null;
		// 0 is also the catalog default for a model without published pricing: say what is known.
		if (perToken === 0) return '$0 / M in the catalog (free, subscription or unpriced)';
		const m = perToken * 1_000_000;
		return `$${m < 0.01 ? m.toFixed(4) : m.toFixed(3)} / M`;
	}

	const groups = $derived.by<Group[]>(() => {
		const out: Group[] = [];
		const push = (title: string, rows: (Row | null)[]) => {
			const kept = rows.filter((r): r is Row => r !== null);
			if (kept.length) out.push({ title, rows: kept });
		};

		const spec = obj(snap.app_requirement);
		push('App spec', [
			row('Spec', spec.name ? `${spec.name}${spec.slug ? ` (${spec.slug})` : ''}` : null),
			row('Version', spec.version != null ? `v${spec.version}` : null, { mono: true }),
			hashRow('Content hash', spec.content_hash),
			row('Schema', spec.spec_schema_version != null ? `v${spec.spec_schema_version}` : null, { mono: true }),
			Object.keys(spec).length
				? {
						label: 'Size',
						value: `${count(spec.api_endpoints)} API · ${count(spec.admin_api_endpoints)} admin endpoints · ${count(spec.backend_requirements)} backend / ${count(spec.frontend_requirements)} frontend requirements`,
					}
				: null,
		]);

		const stack = obj(snap.stack);
		push('Stack scaffold', [
			row('Stack', stack.slug ? `${stack.slug}${stack.version != null ? ` v${stack.version}` : ''}` : null, { mono: true }),
			hashRow('Content hash', stack.content_hash),
		]);

		const brief = obj(snap.brief);
		const templates = obj(snap.prompt_templates);
		const blocks = Array.isArray(snap.blocks) ? (snap.blocks as Record<string, any>[]) : [];
		push(Object.keys(brief).length ? 'Task brief' : 'Prompt profile', [
			row('Brief', brief.version != null ? `v${brief.version}` : null, { mono: true }),
			hashRow('Brief sha256', brief.sha256),
			row('Protected files', brief.protected_files, { mono: true }),
			row('Profile', snap.bundle_slug ? `${snap.bundle_slug}${snap.bundle_version != null ? ` v${snap.bundle_version}` : ''}` : null, { mono: true }),
			row('Prompt stages', Object.keys(templates).length ? Object.keys(templates) : null, { mono: true }),
			blocks.length
				? {
						label: 'Blocks',
						value: blocks.map((b) => `${b.slug ?? b.name}${b.version != null ? `@${b.version}` : ''}`).join(', '),
						mono: true,
					}
				: null,
			hashRow('Prompt hash', snap.prompt_hash ?? job.prompt_hash),
		]);

		const contract = obj(snap.contract);
		push('Conformance contract', [
			row('Version', contract.version != null ? `v${contract.version}` : null, { mono: true }),
			row('Auth paths', [contract.register_path, contract.login_path].filter(Boolean).join(' · ') || null, { mono: true }),
			row('Identity field', contract.identity_field, { mono: true }),
			row('Writes require auth', contract.writes_require_auth),
			row('Admin', contract.admin),
			row('Spec defines auth', contract.spec_defines_auth),
		]);

		const llm = obj(snap.llm);
		const provider = obj(llm.provider);
		push('Model & pricing (frozen at launch)', [
			row('Model', llm.model_slug, { mono: true }),
			row('Served model name', llm.served_model_name, { mono: true }),
			row(
				'Context window',
				typeof llm.context_window === 'number'
					? llm.context_window > 0
						? `${fmtInt(llm.context_window)} tokens${llm.context_window_source ? ` (${String(llm.context_window_source).replace(/_/g, ' ')})` : ''}`
						: `unknown${llm.context_window_source ? ` (${String(llm.context_window_source).replace(/_/g, ' ')})` : ''}`
					: null,
			),
			row('Max output (model)', typeof llm.max_output_tokens === 'number' && llm.max_output_tokens > 0 ? fmtInt(llm.max_output_tokens) : null, { mono: true }),
			row('Max tokens / response', llm.max_tokens, { mono: true }),
			row('Input price', perMillion(llm.input_price_per_token), { mono: true, title: `${llm.input_price_per_token} $/token` }),
			row('Output price', perMillion(llm.output_price_per_token), { mono: true, title: `${llm.output_price_per_token} $/token` }),
			row('Pricing snapshot', llm.pricing_snapshot_at ? new Date(llm.pricing_snapshot_at).toLocaleString() : null),
			row('Provider routing', Object.keys(provider).length ? provider : null, { mono: true }),
			row('Routing source', llm.provider_routing_source ? String(llm.provider_routing_source).replace(/_/g, ' ') : null),
		]);

		const endpoint = obj(snap.endpoint);
		push('Endpoint', [
			row('Endpoint', endpoint.slug ? `${endpoint.slug}${endpoint.kind ? ` (${endpoint.kind})` : ''}` : null, { mono: true }),
			row('Connection', endpoint.connection, { mono: true }),
			row('Quantization', endpoint.quantization, { mono: true }),
			row('Parameter size', endpoint.parameter_size, { mono: true }),
			row('num_ctx', endpoint.num_ctx, { mono: true }),
			hashRow('Weights digest', endpoint.digest),
			row('Max concurrency', endpoint.max_concurrency, { mono: true }),
		]);

		const harness = obj(snap.harness);
		const images = obj(snap.images);
		push('Harness & sandbox', [
			row('Harness', harness.name ? `${harness.name}${harness.version ? `@${harness.version}` : ''}` : null, { mono: true }),
			row('System prompt', harness.system_prompt_source),
			row('Disabled tools', Array.isArray(harness.disabled_tools) && harness.disabled_tools.length ? harness.disabled_tools : null, { mono: true }),
			row('Sandbox revision', harness.sandbox_revision, { mono: true }),
			row('Harness image', images.harness, { mono: true }),
			row('Base image', images.base, { mono: true }),
			row('Gateway image', images.gateway, { mono: true }),
		]);

		const gateway = obj(snap.gateway);
		const stripped = Array.isArray(gateway.stripped_params) ? gateway.stripped_params : [];
		const dropped = Object.keys(obj(gateway.dropped_params));
		push('Gateway policy', [
			row('Sampling policy', gateway.sampling_policy, { mono: true }),
			row('Forced params', Object.keys(obj(gateway.forced_params)).length ? gateway.forced_params : null, { mono: true }),
			stripped.length ? { label: 'Stripped params', value: `${stripped.length}`, mono: true, title: stripped.join(', ') } : null,
			row('Not forwarded', dropped.length ? dropped : null, { mono: true }),
		]);

		const presets = obj(snap.presets);
		push(
			'Preset versions',
			(['harness', 'sampling', 'budget'] as const).map((kind) => {
				const p = obj(presets[kind]);
				if (!p.slug) return null;
				return {
					label: kind.charAt(0).toUpperCase() + kind.slice(1),
					value: `${p.slug} v${p.version ?? '?'} · ${shortHash(p.content_hash)}`,
					mono: true,
					copy: typeof p.content_hash === 'string' ? p.content_hash : undefined,
					title: p.name,
				};
			})
		);
		return out;
	});

	const served = $derived(
		Object.entries(obj(job.result_data?.agent?.served_models)).sort((a, b) => Number(b[1]) - Number(a[1]))
	);
	const requestedName = $derived((obj(snap.llm).served_model_name as string | undefined) ?? (obj(snap.llm).model_slug as string | undefined) ?? null);
</script>

<div class="space-y-3">
	<SectionHeading size="sm" level={3} title="What shaped this generation" lead="Frozen inputs from this run's snapshot, verbatim (not re-read from the current catalog)." />
	{#if groups.length === 0}
		<p class="text-xs text-muted-foreground">This run has no frozen snapshot.</p>
	{:else}
		<div class="grid gap-x-6 gap-y-4 sm:grid-cols-2 xl:grid-cols-3">
			{#each groups as group (group.title)}
				<div class="min-w-0 space-y-1">
					<p class="section-label">{group.title}</p>
					<dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5 text-xs">
						{#each group.rows as r (r.label)}
							<dt class="text-muted-foreground">{r.label}</dt>
							<dd class="min-w-0 {r.mono ? 'font-mono' : ''}">
								{#if r.copy}
									<button
										type="button"
										class="inline-flex max-w-full cursor-pointer items-center gap-1 transition-colors hover:text-primary"
										title="Copy {r.label.toLowerCase()}: {r.copy}"
										onclick={() => copyText(r.copy ?? '', `Copied ${r.label.toLowerCase()}`)}
									>
										<span class="truncate">{r.value}</span><Copy class="size-3 shrink-0" aria-hidden="true" />
									</button>
								{:else}
									<span class="block break-words" title={r.title}>{r.value}</span>
								{/if}
							</dd>
						{/each}
					</dl>
				</div>
			{/each}
			{#if served.length}
				<div class="min-w-0 space-y-1">
					<p class="section-label">Served vs requested</p>
					<p class="text-xs"><span class="text-muted-foreground">Requested</span> <span class="font-mono">{requestedName ?? '—'}</span></p>
					<div class="flex flex-wrap gap-1">
						{#each served as [name, n] (name)}
							<Badge variant={requestedName && name !== requestedName && !requestedName.endsWith(`/${name}`) ? 'outline' : 'secondary'} class="font-mono text-[11px]">{name} · {n} calls</Badge>
						{/each}
					</div>
					<p class="text-[11px] text-muted-foreground">Model names the provider reported per call (the gateway's record).</p>
				</div>
			{/if}
		</div>
	{/if}
</div>
