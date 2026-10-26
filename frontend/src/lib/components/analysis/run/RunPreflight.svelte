<script lang="ts">
	/**
	 * RunPreflight — the mini pre-run report of an ad-hoc run
	 * (`POST /runs/preview/`): blocking issues, warnings, the target's
	 * eligibility and sample, which tools will run on it (readiness, version,
	 * self-test verdict, applicability), the AI reviewer (model, key, cost
	 * estimate), time estimate, analyzer image and the plan hash.
	 *
	 * Props: `preview` (null until the first answer), `loading`, `error`,
	 * `idle` (message while no target is chosen).
	 */
	import type { PreflightIssue, RunPreview } from '$lib/api/analysis';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import { Badge } from '$lib/components/ui/badge';
	import { toneClasses, type Tone } from '$lib/constants/colors';
	import { cn } from '$lib/utils';
	import CopyValue from '../CopyValue.svelte';
	import Hint from '../Hint.svelte';
	import NaValue from '../NaValue.svelte';
	import { fmtInt, fmtSeconds, fmtUsd, shortHash } from '../run-utils';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import Info from '@lucide/svelte/icons/info';
	import KeyRound from '@lucide/svelte/icons/key-round';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';

	interface Props {
		preview: RunPreview | null;
		loading?: boolean;
		error?: string;
		idle?: string;
	}

	let { preview, loading = false, error = '', idle = '' }: Props = $props();

	// The preview leaves out study-design notes (groups, blocks): they say nothing about one run.
	const blocking = $derived(preview?.blocking ?? []);
	const warnings = $derived(preview?.warnings ?? []);
	const info = $derived(preview?.info ?? []);
	const target = $derived(preview?.targets?.[0] ?? null);
	const reviewer = $derived(preview?.reviewer ?? null);
	const est = $derived(preview?.estimates ?? null);

	const verdict = $derived.by((): { tone: Tone; label: string } => {
		if (blocking.length)
			return {
				tone: 'danger',
				label: `${blocking.length} blocking issue${blocking.length === 1 ? '' : 's'}`
			};
		if (target && !target.eligible) return { tone: 'danger', label: 'The target is not eligible' };
		if (warnings.length)
			return {
				tone: 'warning',
				label: `Ready with ${warnings.length} warning${warnings.length === 1 ? '' : 's'}`
			};
		return { tone: 'success', label: 'Ready to run' };
	});

	const languages = $derived(
		target ? Object.entries(target.languages ?? {}).sort((a, b) => b[1] - a[1]) : []
	);

	// The tool table stays folded while every tool is ready and applies
	// cleanly; any problem opens it.
	const readyCount = $derived((preview?.tools ?? []).filter((t) => t.status === 'ready').length);
	// Static tools only: the AI reviewer has its own section below.
	const applies = $derived((preview?.tools ?? []).map((t) => target?.tools?.[t.slug]));
	const runCount = $derived(applies.filter((a) => a === 'run').length);
	const naCount = $derived(applies.filter((a) => a === 'not_applicable').length);
	const toolTrouble = $derived(
		readyCount < (preview?.tools.length ?? 0) ||
			applies.some((a) => a === 'unavailable' || a === 'error')
	);
	let toolsOpen = $state<boolean | null>(null);
	const toolsShown = $derived(toolsOpen ?? toolTrouble);

	const applicabilityTones: Partial<Record<string, Tone>> = {
		run: 'success',
		not_applicable: 'neutral',
		unavailable: 'danger',
		error: 'danger'
	};
</script>

{#snippet issueList(items: PreflightIssue[], tone: Tone)}
	<ul class="space-y-1.5">
		{#each items as item, i (`${item.code}-${i}`)}
			<li class={cn('rounded-md border px-2.5 py-1.5 text-xs', toneClasses[tone])}>
				<span class="font-medium">{item.detail || item.code.replace(/_/g, ' ')}</span>
				{#if item.tools?.length}
					<span class="block text-muted-foreground">Tools: {item.tools.join(', ')}</span>
				{/if}
			</li>
		{/each}
	</ul>
{/snippet}

<div class="space-y-4" aria-live="polite" aria-busy={loading}>
	{#if !preview && !loading && !error}
		<p class="text-sm text-muted-foreground">
			{idle || 'Choose a target to see the preflight.'}
		</p>
	{:else if !preview && loading}
		<p class="flex items-center gap-2 text-sm text-muted-foreground" role="status">
			<LoaderCircle class="size-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />
			Checking the target, tools and reviewer…
		</p>
	{:else if error && !preview}
		<p class={cn('rounded-md border px-3 py-2 text-sm', toneClasses.danger)} role="alert">
			{error}
		</p>
	{/if}

	{#if preview}
		<div class={cn('space-y-4 transition-opacity', loading && 'opacity-60')}>
			{#if error}
				<p class={cn('rounded-md border px-3 py-2 text-xs', toneClasses.danger)} role="alert">
					{error} (showing the previous check)
				</p>
			{/if}
			<div
				class={cn(
					'flex items-center gap-2 rounded-md border px-3 py-2 text-sm font-medium',
					toneClasses[verdict.tone]
				)}
			>
				{#if verdict.tone === 'success'}
					<CircleCheck class="size-4 shrink-0" aria-hidden="true" />
				{:else if verdict.tone === 'warning'}
					<TriangleAlert class="size-4 shrink-0" aria-hidden="true" />
				{:else}
					<CircleAlert class="size-4 shrink-0" aria-hidden="true" />
				{/if}
				<span class="min-w-0 flex-1">{verdict.label}</span>
				{#if loading}
					<LoaderCircle
						class="size-3.5 animate-spin motion-reduce:animate-none"
						aria-label="Updating"
					/>
				{/if}
			</div>

			{#if blocking.length}{@render issueList(blocking, 'danger')}{/if}
			{#if warnings.length}{@render issueList(warnings, 'warning')}{/if}

			<!-- Target -->
			{#if target}
				<section aria-labelledby="pf-target" class="space-y-1.5">
					<h3 id="pf-target" class="section-label">Target</h3>
					<p class="break-words font-mono text-xs">{target.label}</p>
					<div class="flex flex-wrap items-center gap-1.5">
						<StatusBadge
							status={target.eligible ? 'eligible' : 'not eligible'}
							tone={target.eligible ? 'success' : 'danger'}
						/>
						{#if target.source}<Badge variant="outline">{target.source}</Badge>{/if}
					</div>
					{#if target.reasons.length}
						<ul class="space-y-0.5 text-xs">
							{#each target.reasons as reason, i (`${reason.code}-${i}`)}
								<li class="flex items-start gap-1.5">
									{#if reason.severity === 'exclude'}
										<CircleAlert
											class="mt-px size-3.5 shrink-0 text-danger-fg"
											aria-label="excludes the target"
										/>
									{:else if reason.severity === 'warn'}
										<TriangleAlert
											class="mt-px size-3.5 shrink-0 text-warning-fg"
											aria-label="warning"
										/>
									{:else}
										<Info class="mt-px size-3.5 shrink-0 text-muted-foreground" aria-label="note" />
									{/if}
									<span>{reason.detail}</span>
								</li>
							{/each}
						</ul>
					{/if}
					<dl class="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
						<dt class="text-muted-foreground">Files</dt>
						<dd class="text-right tabular-nums">
							{target.files} included of {target.files_total} · {target.files_measured}
							measured
						</dd>
						<dt class="text-muted-foreground">SLOC</dt>
						<dd class="text-right tabular-nums">
							{fmtInt(target.sloc_measured)} measured of {fmtInt(target.sloc_total)}
						</dd>
					</dl>
					{#if languages.length}
						<div class="flex flex-wrap gap-1">
							{#each languages as [lang, sloc] (lang)}
								<span class="rounded border border-border px-1.5 text-[11px]"
									>{lang}
									<span class="text-muted-foreground tabular-nums">{sloc}</span></span
								>
							{/each}
						</div>
					{/if}
					{#if target.warnings.length}
						<ul class="list-disc space-y-0.5 pl-4 text-[11px] text-warning-fg">
							{#each target.warnings as w, i (i)}<li>{w}</li>{/each}
						</ul>
					{/if}
				</section>
			{/if}

			<!-- Tools -->
			{#if preview.tools.length || (reviewer && reviewer.enabled)}
				<section aria-labelledby="pf-tools" class="space-y-1.5">
					<div class="flex flex-wrap items-center justify-between gap-2">
						<h3 id="pf-tools" class="section-label">Tools</h3>
						<button
							type="button"
							class="cursor-pointer text-[11px] font-medium text-primary underline-offset-2 hover:underline"
							aria-expanded={toolsShown}
							aria-controls="pf-tools-table"
							onclick={() => (toolsOpen = !toolsShown)}
							>{toolsShown ? 'Hide tools' : 'Show tools'}</button
						>
					</div>
					<p class="text-xs">
						<span class="font-medium tabular-nums">{readyCount}/{preview.tools.length}</span>
						ready on the analyzer image ·
						<span class="font-medium tabular-nums">{runCount}</span>
						run on this target{#if naCount > 0}{' · '}{naCount} not applicable{/if}
					</p>
					<div id="pf-tools-table" class="table-scroll-wrapper -mx-1" hidden={!toolsShown}>
						<table class="text-xs">
							<thead>
								<tr>
									<th>Tool</th>
									<th>Ready</th>
									<th>On target</th>
								</tr>
							</thead>
							<tbody>
								{#each preview.tools as tool (tool.slug)}
									{@const applies = target?.tools?.[tool.slug]}
									<tr>
										<td>
											<span class="font-mono">{tool.slug}</span>
											<span class="block text-[11px] text-muted-foreground">
												{tool.version || '—'}{tool.version_source !== 'image_manifest'
													? ' (unverified)'
													: ''} ·
												{tool.required ? 'required' : 'optional'}
											</span>
										</td>
										<td>
											<Hint
												text={tool.canary.verdict === 'passed'
													? 'Passed the latest self-test on this image'
													: tool.canary.verdict === 'failed'
														? `Failed the latest self-test${tool.canary.missing?.length ? `: missed ${tool.canary.missing.map((m) => m.rule).join(', ')}` : ''}`
														: tool.canary.verdict === 'config_untested'
															? "Self-tested only at another configuration (the default profile's): no positive control covers this plan's configuration"
															: 'No self-test evidence on this image: not proof that the tool works'}
												underline={false}
											>
												<StatusBadge status={tool.status} />
											</Hint>
										</td>
										<td class="whitespace-nowrap">
											{#if applies}
												<StatusBadge
													status={applies}
													tones={applicabilityTones}
													label={applies === 'run' ? 'runs' : applies.replace(/_/g, ' ')}
												/>
												{#if applies === 'run' && target?.tool_files?.[tool.slug] != null}
													<span class="ml-1 text-[11px] text-muted-foreground tabular-nums"
														>{target.tool_files[tool.slug]}
														{target.tool_files[tool.slug] === 1 ? 'file' : 'files'}</span
													>
												{/if}
											{:else}
												<span class="text-muted-foreground">—</span>
											{/if}
										</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				</section>
			{/if}

			<!-- AI reviewer -->
			<section aria-labelledby="pf-ai" class="space-y-1.5">
				<h3 id="pf-ai" class="section-label">AI reviewer</h3>
				{#if !reviewer || !reviewer.enabled}
					<p class="text-xs text-muted-foreground">Off in this profile: static tools only.</p>
				{:else}
					<dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
						<dt class="text-muted-foreground">Model</dt>
						<dd class="min-w-0 break-all text-right font-mono">
							{reviewer.model}
							{#if !reviewer.model_found}<span class="block font-sans text-danger-fg"
									>not in the model catalog</span
								>{/if}
						</dd>
						<dt class="text-muted-foreground">Engine</dt>
						<dd class="text-right">
							{reviewer.engine.replace(/_/g, '-')}{reviewer.harness ? ` · ${reviewer.harness}` : ''}
							· k = {reviewer.samples}
						</dd>
						<dt class="text-muted-foreground">Structured output</dt>
						<dd class="text-right">
							{reviewer.structured_outputs ? 'json schema' : 'json object + local validation'}
						</dd>
						<dt class="text-muted-foreground">Est. cost</dt>
						<dd class="text-right tabular-nums">
							{fmtUsd(reviewer.cost_estimate.expected)}
							<span class="text-muted-foreground"
								>({fmtUsd(reviewer.cost_estimate.low)}–{fmtUsd(reviewer.cost_estimate.high)})</span
							>
						</dd>
					</dl>
					{#if !reviewer.key_present}
						<p
							class={cn(
								'flex items-start gap-1.5 rounded-md border px-2.5 py-1.5 text-xs',
								toneClasses.warning
							)}
						>
							<KeyRound class="mt-px size-3.5 shrink-0" aria-hidden="true" />
							<span>
								No OpenRouter API key is available, so the run would be refused.
								<a
									href="/users/settings#credentials"
									class="font-medium text-primary underline-offset-2 hover:underline">Add a key</a
								>
							</span>
						</p>
					{/if}
				{/if}
			</section>

			<!-- Estimates + provenance -->
			{#if est}
				<section aria-labelledby="pf-est" class="space-y-1.5">
					<h3 id="pf-est" class="section-label">Estimate</h3>
					<dl class="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
						<dt class="text-muted-foreground">Tool executions</dt>
						<dd class="text-right tabular-nums">{est.tool_executions}</dd>
						<dt class="text-muted-foreground">AI calls</dt>
						<dd class="text-right tabular-nums">{est.ai_calls}</dd>
						<dt class="text-muted-foreground">Wall time</dt>
						<dd class="text-right tabular-nums">
							~{fmtSeconds(est.wall_seconds)}
						</dd>
						<dt class="text-muted-foreground">AI cost</dt>
						<dd class="text-right tabular-nums">
							{#if est.ai_cost_usd == null}<NaValue
									reason="No cost estimate (no AI reviewer or no price)"
								/>{:else}{fmtUsd(est.ai_cost_usd)}{/if}
						</dd>
					</dl>
				</section>
			{/if}

			<section aria-labelledby="pf-plan" class="space-y-1.5">
				<h3 id="pf-plan" class="section-label">Plan</h3>
				<dl class="grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-3 gap-y-1 text-xs">
					<dt class="text-muted-foreground">Plan hash</dt>
					<dd class="min-w-0 justify-self-end">
						<CopyValue
							value={preview.plan_hash}
							display={shortHash(preview.plan_hash, 12)}
							label="plan hash"
						/>
					</dd>
					<dt class="text-muted-foreground">Analyzer image</dt>
					<dd class="min-w-0 text-right">
						<span class="break-all font-mono">{preview.image.tag || '—'}</span>
						{#if !preview.image.daemon_ok}
							<span class="block text-danger-fg">analysis Docker daemon unreachable</span>
						{:else if !preview.image.built}
							<span class="block text-warning-fg"
								>{preview.image.building
									? 'building…'
									: 'not built: tool versions are unverified'}</span
							>
						{/if}
					</dd>
				</dl>
			</section>

			{#if info.length}
				<ul class="space-y-0.5 text-[11px] text-muted-foreground">
					{#each info as item, i (`${item.code}-${i}`)}<li>
							{item.detail}
						</li>{/each}
				</ul>
			{/if}
		</div>
	{/if}
</div>
