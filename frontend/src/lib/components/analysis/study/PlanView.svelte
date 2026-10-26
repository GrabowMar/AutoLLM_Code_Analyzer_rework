<script lang="ts">
	/**
	 * PlanView — read-only view of a frozen analysis plan (what every run of
	 * a locked study executes): tools with verified versions and config
	 * hashes, the resolved AI reviewer, scope, outcome settings, engine
	 * versions and mapping hashes. `plan_hash` equal ⇔ comparable runs.
	 *
	 * Props: `plan: FrozenPlan`, `planHash`.
	 */
	import { SEVERITIES, type FrozenPlan } from '$lib/api/analysis';
	import * as Card from '$lib/components/ui/card';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import Hint from '../Hint.svelte';
	import { fmtBytes, fmtDateTime, fmtInt, fmtSeconds, fmtUsd, shortHash } from './format';

	interface Props {
		plan: FrozenPlan;
		planHash: string;
	}

	let { plan, planHash }: Props = $props();

	const reviewer = $derived(plan.reviewer);

	function configText(value: unknown): string {
		if (Array.isArray(value)) return value.join(', ') || '[]';
		if (value && typeof value === 'object') return JSON.stringify(value);
		return value === '' ? '""' : String(value);
	}
</script>

<div class="space-y-4">
	<dl
		class="grid gap-x-6 gap-y-2 rounded-md border border-border bg-card px-4 py-3 text-sm sm:grid-cols-2 lg:grid-cols-4"
	>
		<div>
			<dt class="section-label">Plan hash</dt>
			<dd class="font-mono text-xs" title={planHash}>
				#{shortHash(planHash, 16)}
			</dd>
		</div>
		<div>
			<dt class="section-label">Profile</dt>
			<dd>
				{#if plan.profile}
					<a class="hover:text-primary hover:underline" href="/analysis/profiles/{plan.profile.id}"
						>{plan.profile.slug}@v{plan.profile.version}</a
					>
					<span
						class="font-mono text-[11px] text-muted-foreground"
						title={plan.profile.content_hash}>#{shortHash(plan.profile.content_hash, 8)}</span
					>
				{:else}
					inline config
				{/if}
			</dd>
		</div>
		<div>
			<dt class="section-label">Analyzer image</dt>
			<dd class="truncate font-mono text-xs" title={plan.provenance.image_id}>
				{plan.analyzer_image.tag}
			</dd>
		</div>
		<div>
			<dt class="section-label">Frozen</dt>
			<dd>{fmtDateTime(plan.provenance.frozen_at)}</dd>
		</div>
	</dl>

	<Card.Root>
		<Card.Header><Card.Title>Tools ({plan.tools.length})</Card.Title></Card.Header>
		<Card.Content class="px-0">
			<div class="table-scroll-wrapper">
				<table>
					<thead>
						<tr>
							<th scope="col">Tool</th>
							<th scope="col">Category</th>
							<th scope="col">Version</th>
							<th scope="col">Config</th>
						</tr>
					</thead>
					<tbody>
						{#each plan.tools as tool (tool.slug)}
							<tr>
								<td>
									<span class="font-medium">{tool.name}</span>
									<span class="ml-1 text-[11px] text-muted-foreground"
										>{tool.required ? 'required' : 'optional'}</span
									>
								</td>
								<td class="text-xs text-muted-foreground">{tool.category} · {tool.kind}</td>
								<td class="whitespace-nowrap">
									<span class="font-mono text-xs">{tool.version || '—'}</span>
									{#if tool.version_source !== 'image_manifest'}
										<Hint
											underline={false}
											text="Frozen before the image was built: the version is the catalog's expectation, not verified."
										>
											<span class="text-[11px] text-warning-fg">unverified</span>
										</Hint>
									{/if}
								</td>
								<td>
									{#if Object.keys(tool.config).length === 0}
										<span
											class="font-mono text-[11px] text-muted-foreground"
											title={tool.config_hash}>defaults #{shortHash(tool.config_hash, 8)}</span
										>
									{:else}
										<details class="group">
											<summary
												class="flex cursor-pointer list-none items-center gap-1 font-mono text-[11px] text-muted-foreground"
											>
												<ChevronRight
													class="h-3 w-3 transition-transform group-open:rotate-90"
													aria-hidden="true"
												/>
												#{shortHash(tool.config_hash, 8)} · {Object.keys(tool.config).length} option{Object.keys(
													tool.config
												).length === 1
													? ''
													: 's'}
											</summary>
											<dl
												class="mt-1 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5 text-[11px]"
											>
												{#each Object.entries(tool.config) as [k, v] (k)}
													<dt class="font-mono text-muted-foreground">{k}</dt>
													<dd class="break-all font-mono">{configText(v)}</dd>
												{/each}
											</dl>
										</details>
									{/if}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</Card.Content>
	</Card.Root>

	<div class="grid gap-4 xl:grid-cols-2">
		<Card.Root>
			<Card.Header><Card.Title>AI reviewer</Card.Title></Card.Header>
			<Card.Content>
				{#if !reviewer}
					<p class="text-sm text-muted-foreground">None: static tools only.</p>
				{:else}
					<dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm">
						<dt class="text-muted-foreground">Model</dt>
						<dd class="break-all font-mono text-xs" title={reviewer.model.canonical_slug}>
							{reviewer.model.slug}
						</dd>
						<dt class="text-muted-foreground">Engine</dt>
						<dd>
							{reviewer.engine === 'agent'
								? `agent · ${reviewer.harness} ${reviewer.harness_version}`
								: 'single-shot'} · {reviewer.output_mode}
						</dd>
						<dt class="text-muted-foreground">Samples</dt>
						<dd>
							k = {reviewer.samples} · quorum {reviewer.quorum} · {reviewer.required
								? 'required'
								: 'optional'}
						</dd>
						<dt class="text-muted-foreground">Parameters</dt>
						<dd class="text-xs">
							temperature {reviewer.params.temperature ?? 'default'} · max {fmtInt(
								reviewer.params.max_tokens
							)} tokens · reasoning
							{reviewer.params.reasoning?.effort ?? 'default'}
						</dd>
						<dt class="text-muted-foreground">Prompt / schema</dt>
						<dd class="font-mono text-xs">
							{reviewer.prompt_version} #{shortHash(reviewer.prompt_sha256, 8)} ·
							{reviewer.schema_version} #{shortHash(reviewer.schema_sha256, 8)}
						</dd>
						<dt class="text-muted-foreground">Budget / sample</dt>
						<dd class="text-xs">
							{fmtUsd(reviewer.budget.max_cost_usd)} · {reviewer.budget.max_llm_requests} calls · {fmtSeconds(
								reviewer.budget.wall_clock_seconds
							)}
						</dd>
					</dl>
				{/if}
			</Card.Content>
		</Card.Root>

		<Card.Root>
			<Card.Header><Card.Title>Scope and outcomes</Card.Title></Card.Header>
			<Card.Content>
				<dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm">
					<dt class="text-muted-foreground">Measured origins</dt>
					<dd>{plan.scope.measured_origins.join(', ')}</dd>
					<dt class="text-muted-foreground">Measured kinds</dt>
					<dd>{plan.scope.measured_kinds.join(', ')}</dd>
					<dt class="text-muted-foreground">Line attribution</dt>
					<dd>
						{plan.scope.line_attribution ? 'on (modified files: changed lines only)' : 'off'}
					</dd>
					<dt class="text-muted-foreground">Excluded</dt>
					<dd class="text-xs">
						kinds {plan.scope.exclude_kinds.join(', ') || 'none'} · globs {plan.scope.exclude_globs.join(
							', '
						) || 'none'} · files over
						{fmtBytes(plan.scope.max_file_bytes)}
					</dd>
					<dt class="text-muted-foreground">Density floor</dt>
					<dd>NA below {plan.outcomes.min_sloc_for_density} measured SLOC</dd>
					<dt class="text-muted-foreground">Counting</dt>
					<dd class="text-xs">
						style {plan.outcomes.count_style ? 'counted' : 'not counted'} · metric findings {plan
							.outcomes.count_metric_findings
							? 'counted'
							: 'not counted'}
					</dd>
					<dt class="text-muted-foreground">Severity weights</dt>
					<dd class="text-xs">
						{SEVERITIES.filter((s) => s in plan.outcomes.severity_weights)
							.map((s) => `${s} ${plan.outcomes.severity_weights[s]}`)
							.join(' · ')}
					</dd>
					<dt class="text-muted-foreground">Engines</dt>
					<dd class="font-mono text-[11px]">
						{Object.entries(plan.engine_versions)
							.map(([k, v]) => `${k}=${v}`)
							.join(' ')}
					</dd>
				</dl>
			</Card.Content>
		</Card.Root>
	</div>

	{#if plan.notes.length > 0}
		<ul class="space-y-1 text-xs text-warning-fg">
			{#each plan.notes as note, i (i)}<li>
					{note.code}: {note.detail}
				</li>{/each}
		</ul>
	{/if}
</div>
