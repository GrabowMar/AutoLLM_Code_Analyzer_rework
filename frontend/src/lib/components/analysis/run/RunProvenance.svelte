<script lang="ts">
	/**
	 * RunProvenance — everything needed to reproduce and compare a run: the
	 * plan and sample hashes, profile version, analyzer image and its
	 * manifest digest, tool versions/configs/hashes, scope and outcome rules,
	 * engine and mapping versions, the analysed target's metadata, and the
	 * execution record.
	 *
	 * Props: `run`.
	 */
	import type { AnalysisRunDetail } from '$lib/api/analysis';
	import { runExportUrl } from '$lib/api/analysis';
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import CopyValue from '../CopyValue.svelte';
	import Hint from '../Hint.svelte';
	import { fmtDateTime, fmtInt, fmtRate, fmtSeconds, fmtUsd, shortHash } from '../run-utils';
	import Download from '@lucide/svelte/icons/download';

	interface Props {
		run: AnalysisRunDetail;
	}

	let { run }: Props = $props();

	const plan = $derived(run.plan);
	const meta = $derived(run.target_meta ?? ({} as AnalysisRunDetail['target_meta']));
	const digest = $derived(plan?.provenance?.image_manifest ?? null);

	function text(value: unknown): string {
		if (value === null || value === undefined || value === '') return '—';
		if (Array.isArray(value)) return value.length ? value.join(', ') : '—';
		return String(value);
	}

	const targetRows = $derived<[string, string][]>(
		[
			['Mode', meta.mode],
			[
				'Model',
				meta.model_name ? `${meta.model_name} (${meta.model_slug ?? '—'})` : meta.model_slug
			],
			['Model canonical slug', meta.model_canonical_slug],
			[
				'Provider / family',
				[meta.model_provider, meta.generator_family].filter(Boolean).join(' / ')
			],
			['App', meta.app_name ? `${meta.app_name} (${meta.app_slug ?? '—'})` : meta.app_slug],
			[
				'Harness',
				meta.harness
					? `${meta.harness}${meta.harness_version ? ` ${meta.harness_version}` : ''}`
					: null
			],
			[
				'Stack',
				meta.stack_slug
					? `${meta.stack_slug}${meta.stack_version != null ? `@${meta.stack_version}` : ''}`
					: null
			],
			['Prompt hash', meta.prompt_hash],
			['Bundle key', meta.bundle_key],
			['Method key', meta.method_key],
			['Config fingerprint', meta.config_fingerprint],
			[
				'Condition',
				meta.condition_label ?? (meta.condition_id != null ? String(meta.condition_id) : null)
			],
			[
				'Repeat / seed',
				meta.repeat_index != null || meta.seed != null
					? `r${meta.repeat_index ?? '—'} · seed ${meta.seed ?? '—'}`
					: null
			],
			['Termination', meta.termination],
			['Generated', meta.created_at ? fmtDateTime(meta.created_at) : null]
		]
			.map(([k, v]) => [k as string, text(v)] as [string, string])
			.filter(([, v]) => v !== '—')
	);

	const quality = $derived.by(() => {
		const out: [string, string][] = [];
		const v = meta.validity;
		if (v) {
			out.push([
				'Validity',
				`${v.valid == null ? 'unknown' : v.valid ? 'valid' : 'invalid'}${v.exclude ? ' · excluded' : ''} (${v.source})`
			]);
			for (const note of v.notes ?? [])
				out.push(['Validity note', [note.code, note.detail].filter(Boolean).join(': ')]);
		}
		if (meta.truncated !== undefined) {
			out.push([
				'Truncated',
				meta.truncated
					? `yes (${(meta.truncation_reasons ?? []).join(', ') || 'unspecified'})`
					: `no${meta.truncation?.evidence ? ` (evidence: ${meta.truncation.evidence})` : ''}`
			]);
		}
		if (meta.empty_output !== undefined)
			out.push(['Empty output', meta.empty_output ? 'yes' : 'no']);
		if (meta.functional) {
			out.push([
				'Functional',
				`${meta.functional.status.replace(/_/g, ' ')}${meta.functional.pass_rate != null ? ` · ${fmtRate(meta.functional.pass_rate)}` : ''}${
					meta.functional.source ? ` · ${meta.functional.source}` : ''
				}${meta.functional.evaluator_version ? ` (${meta.functional.evaluator_version})` : ''}`
			]);
		}
		if (meta.generation) {
			out.push([
				'Generation',
				`${fmtUsd(meta.generation.cost)} · ${fmtInt(meta.generation.total_tokens)} tokens · ${fmtSeconds(meta.generation.duration_seconds)}`
			]);
		}
		return out;
	});
</script>

{#snippet kv(rows: [string, string][])}
	<dl
		class="grid grid-cols-[minmax(0,7.5rem)_minmax(0,1fr)] sm:grid-cols-[minmax(0,10rem)_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm"
	>
		{#each rows as [k, v], i (i)}
			<dt class="text-muted-foreground">{k}</dt>
			<dd class="min-w-0 break-words">{v}</dd>
		{/each}
	</dl>
{/snippet}

<div class="space-y-4">
	<div class="grid gap-4 lg:grid-cols-2">
		<Card.Root>
			<Card.Header>
				<Card.Title>Comparability</Card.Title>
				<Card.Description>
					Runs are comparable only when their plan hashes are equal (the profile itself is not
					hashed).
				</Card.Description>
			</Card.Header>
			<Card.Content>
				<dl
					class="grid grid-cols-[minmax(0,7.5rem)_minmax(0,1fr)] sm:grid-cols-[minmax(0,10rem)_minmax(0,1fr)] items-center gap-x-4 gap-y-1.5 text-sm"
				>
					<dt class="text-muted-foreground">Plan hash</dt>
					<dd class="min-w-0">
						{#if run.plan_hash}
							<CopyValue
								value={run.plan_hash}
								display={shortHash(run.plan_hash, 20)}
								label="plan hash"
							/>
							<a
								href="/analysis?plan_hash={encodeURIComponent(run.plan_hash)}"
								class="block text-xs text-primary underline-offset-2 hover:underline"
								>Runs with this plan</a
							>
						{:else}—{/if}
					</dd>
					<dt class="text-muted-foreground">Sample hash</dt>
					<dd class="min-w-0">
						{#if run.sample_hash}<CopyValue
								value={run.sample_hash}
								display={shortHash(run.sample_hash, 20)}
								label="sample hash"
							/>{:else}—{/if}
					</dd>
					<dt class="text-muted-foreground">Profile</dt>
					<dd class="min-w-0">
						{#if plan?.profile}
							<a
								href="/analysis/profiles/{plan.profile.id}"
								class="text-primary underline-offset-2 hover:underline"
								>{plan.profile.slug}@v{plan.profile.version}</a
							>
							<span
								class="block font-mono text-[11px] text-muted-foreground"
								title={plan.profile.content_hash}
							>
								content {shortHash(plan.profile.content_hash, 16)}
							</span>
						{:else}
							<span class="text-muted-foreground">inline config (no profile)</span>
						{/if}
					</dd>
					<dt class="text-muted-foreground">Analyzer image</dt>
					<dd class="min-w-0">
						<span class="break-all font-mono text-xs">{plan?.analyzer_image?.tag ?? '—'}</span>
						{#if plan?.provenance?.image_id}
							<CopyValue
								value={plan.provenance.image_id}
								display={shortHash(plan.provenance.image_id.replace(/^sha256:/, ''), 16)}
								label="image id"
								class="flex"
							/>
						{:else}
							<span class="block text-[11px] text-warning-fg"
								>not built when frozen: tool versions are unverified</span
							>
						{/if}
					</dd>
					<dt class="text-muted-foreground">Frozen</dt>
					<dd>{fmtDateTime(plan?.provenance?.frozen_at)}</dd>
					<dt class="text-muted-foreground">Engine</dt>
					<dd class="text-xs">
						{Object.entries(plan?.engine_versions ?? {})
							.map(([k, v]) => `${k.replace(/_/g, ' ')} ${v}`)
							.join(' · ') || '—'}
					</dd>
					<dt class="text-muted-foreground">Mappings</dt>
					<dd class="flex flex-wrap gap-x-3 gap-y-0.5 text-xs">
						{#each Object.entries(plan?.mapping_hashes ?? {}) as [name, hash] (name)}
							<span
								><span class="text-muted-foreground">{name.replace(/_/g, ' ')}</span>
								<span class="font-mono" title={hash}>{shortHash(hash, 8)}</span></span
							>
						{:else}—{/each}
					</dd>
				</dl>
				{#if plan?.notes?.length}
					<ul class="mt-3 list-disc space-y-0.5 pl-4 text-xs text-warning-fg">
						{#each plan.notes as note, i (i)}<li>
								{note.detail || note.code}
							</li>{/each}
					</ul>
				{/if}
				<Button
					variant="outline"
					size="sm"
					class="mt-4"
					href={runExportUrl(run.id, 'json')}
					download
				>
					<Download class="size-3.5" />Full run export (JSON)
				</Button>
			</Card.Content>
		</Card.Root>

		<Card.Root>
			<Card.Header>
				<Card.Title>Target</Card.Title>
				<Card.Description>
					{#if run.generation_job_id}
						Generation job
						<a
							href="/applications/{run.generation_job_id}"
							class="font-mono text-primary underline-offset-2 hover:underline"
							>{run.generation_job_id.slice(0, 8)}</a
						>, snapshotted when the run was submitted.
					{:else}
						Pasted source (non-research): no model, app or generation metadata.
					{/if}
				</Card.Description>
			</Card.Header>
			<Card.Content class="space-y-4">
				{#if targetRows.length}{@render kv(targetRows)}{/if}
				{#if quality.length}
					<div>
						<p class="section-label mb-1.5">Sample quality</p>
						{@render kv(quality)}
					</div>
				{/if}
				{#if meta.experiment_id || meta.batch_id}
					<div class="flex flex-wrap gap-2">
						{#if meta.experiment_id}
							<Button variant="outline" size="sm" href="/experiments/{meta.experiment_id}"
								>Experiment</Button
							>
						{/if}
						{#if meta.batch_id}
							<Button variant="outline" size="sm" href="/sample-generator/batches/{meta.batch_id}"
								>Batch</Button
							>
						{/if}
					</div>
				{/if}
			</Card.Content>
		</Card.Root>
	</div>

	<Card.Root>
		<Card.Header>
			<Card.Title>Tools in the plan</Card.Title>
			<Card.Description
				>Versions come from the analyzer image manifest; configs are hashed into the plan.</Card.Description
			>
		</Card.Header>
		<Card.Content class="p-0">
			<div class="table-scroll-wrapper">
				<table class="min-w-full">
					<thead>
						<tr>
							<th>Tool</th>
							<th>Required</th>
							<th>Version</th>
							<th class="hidden md:table-cell">Languages</th>
							<th>Config</th>
							<th class="hidden lg:table-cell">Spec</th>
						</tr>
					</thead>
					<tbody>
						{#each plan?.tools ?? [] as tool (tool.slug)}
							<tr>
								<td>
									<span class="font-mono text-xs font-medium">{tool.slug}</span>
									<span class="block text-[11px] text-muted-foreground"
										>{tool.kind} · {tool.category.replace(/_/g, ' ')}</span
									>
								</td>
								<td class="text-xs">{tool.required ? 'required' : 'optional'}</td>
								<td class="whitespace-nowrap">
									<span class="font-mono text-xs">{tool.version || '—'}</span>
									{#if tool.version_source === 'image_manifest'}
										<Badge variant="outline" class="ml-1 border-success/30 text-success-fg"
											>verified</Badge
										>
									{:else}
										<Hint
											text="The analyzer image was not built when the plan was frozen; this is the catalog's expected version"
											underline={false}
										>
											<Badge variant="outline" class="ml-1 border-warning/30 text-warning-fg"
												>unverified</Badge
											>
										</Hint>
									{/if}
								</td>
								<td class="hidden max-w-48 text-xs text-muted-foreground md:table-cell"
									>{tool.languages.join(', ') || 'any'}</td
								>
								<td class="text-xs">
									<details>
										<summary class="cursor-pointer select-none font-mono" title={tool.config_hash}
											>{shortHash(tool.config_hash, 10)}</summary
										>
										<pre class="terminal mt-1 max-h-48 max-w-md">{JSON.stringify(
												tool.config,
												null,
												2
											)}</pre>
									</details>
								</td>
								<td class="hidden font-mono text-xs lg:table-cell" title={tool.spec_hash}
									>{shortHash(tool.spec_hash, 10)}</td
								>
							</tr>
						{:else}
							<tr
								><td colspan="6" class="text-sm text-muted-foreground"
									>No static tools in this plan.</td
								></tr
							>
						{/each}
					</tbody>
				</table>
			</div>
		</Card.Content>
	</Card.Root>

	<div class="grid gap-4 lg:grid-cols-2">
		<Card.Root>
			<Card.Header>
				<Card.Title>Scope and outcome rules</Card.Title>
				<Card.Description
					>Findings are counted and SLOC measured over the same files and lines.</Card.Description
				>
			</Card.Header>
			<Card.Content>
				{#if plan?.scope && plan?.outcomes}
					{@render kv([
						['Measured origins', text(plan.scope.measured_origins)],
						['Measured kinds', text(plan.scope.measured_kinds)],
						[
							'Line attribution',
							plan.scope.line_attribution
								? 'yes (modified files: changed lines only)'
								: 'no (whole files)'
						],
						['Excluded kinds', text(plan.scope.exclude_kinds)],
						['Excluded globs', text(plan.scope.exclude_globs)],
						['Max file size', `${fmtInt(plan.scope.max_file_bytes)} bytes`],
						['Density floor', `${plan.outcomes.min_sloc_for_density} measured SLOC`],
						['Count style', plan.outcomes.count_style ? 'yes' : 'no'],
						['Count metric findings', plan.outcomes.count_metric_findings ? 'yes' : 'no'],
						[
							'Severity weights',
							Object.entries(plan.outcomes.severity_weights)
								.map(([k, v]) => `${k} ${v}`)
								.join(' · ')
						]
					])}
				{:else}
					<p class="text-sm text-muted-foreground">Not recorded.</p>
				{/if}
			</Card.Content>
		</Card.Root>

		<Card.Root>
			<Card.Header>
				<Card.Title>Image manifest</Card.Title>
				<Card.Description
					>Digest of the analyzer image's manifest frozen with the plan.</Card.Description
				>
			</Card.Header>
			<Card.Content>
				{#if digest}
					<p class="mb-2 text-xs text-muted-foreground">
						built {fmtDateTime(digest.built_at)} · release cutoff {digest.release_cutoff ??
							'—'}{#if digest.runtime}{' · '}python {digest.runtime.python} · node
							{digest.runtime.node} · {digest.runtime.os}{/if}
					</p>
					<div class="table-scroll-wrapper max-h-80">
						<table class="text-xs">
							<thead><tr><th>Tool</th><th>Version</th><th>Runtime</th><th>Lock</th></tr></thead>
							<tbody>
								{#each Object.entries(digest.tools ?? {}) as [slug, t] (slug)}
									<tr>
										<td class="font-mono">{slug}</td>
										<td class="font-mono">{t.version}</td>
										<td>{t.runtime ?? '—'}</td>
										<td class="font-mono" title={String(t.lock_sha256 ?? t.sha256 ?? '')}
											>{shortHash(String(t.lock_sha256 ?? t.sha256 ?? ''), 10)}</td
										>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				{:else}
					<p class="text-sm text-muted-foreground">
						No manifest digest: the image was not built when this plan was frozen, so versions are
						unverified.
					</p>
				{/if}
			</Card.Content>
		</Card.Root>
	</div>

	<Card.Root>
		<Card.Header>
			<Card.Title>Execution</Card.Title>
		</Card.Header>
		<Card.Content>
			{@render kv([
				['Run id', run.id],
				['Attempt', String(run.attempt)],
				['Created', fmtDateTime(run.created_at)],
				['Started', fmtDateTime(run.started_at)],
				['Completed', fmtDateTime(run.completed_at)],
				['Duration', fmtSeconds(run.duration_seconds)],
				['Last heartbeat', fmtDateTime(run.heartbeat_at)],
				['Worker lease', run.lease_owner || '—'],
				['Updated', fmtDateTime(run.updated_at)]
			])}
		</Card.Content>
	</Card.Root>
</div>
