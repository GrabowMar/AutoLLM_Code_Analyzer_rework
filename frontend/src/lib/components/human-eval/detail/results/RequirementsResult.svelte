<script lang="ts">
	/**
	 * RequirementsResult — requirement compliance (met 1, partial ½, not met /
	 * blocked 0; can't-test excluded, strict counts it as 0) per group and per
	 * app next to the automated conformance pass rates, verdict counts per
	 * requirement, agreement (ordinal), convergent validity against the
	 * conformance suite (Spearman on within-spec ranks: endpoints primary,
	 * overall secondary) and endpoint-level agreement (Cohen's kappa).
	 *
	 * Props: `result` (RequirementsBlockResult), `section`, `ctx`.
	 */
	import StudyFigure from '$lib/components/experiments/study/StudyFigure.svelte';
	import type { RequirementsBlockResult } from '$lib/api/human-eval';
	import { formatCi, formatCoef, formatShare } from '../../format';
	import AgreementLine from './AgreementLine.svelte';
	import GroupScoreFigure from './GroupScoreFigure.svelte';
	import type { ResultsContext } from './types';

	let { result, section, ctx }: { result: RequirementsBlockResult; section: string; ctx: ResultsContext } = $props();

	const isPrimary = (metric: string) => ctx.primary?.block_id === result.block_id && ctx.primary.metric === metric;
	const share = (v: number) => formatShare(v);
	const share1 = (v: number) => formatShare(v, 1);
	const coef = (v: number) => formatCoef(v);
	const sources = $derived([...new Set(result.per_app.flatMap((a) => Object.keys(a.by_source ?? {})))].sort());
	const ea = $derived(result.endpoint_agreement);
</script>

<div class="space-y-4">
	<GroupScoreFigure
		id="fig-{result.block_id}-compliance"
		number="{section}.1"
		title="Requirement compliance by {ctx.groupNoun.toLowerCase()}"
		scores={result.per_group}
		domain={[0, 1]}
		tickKind="share"
		format={share1}
		groupNoun={ctx.groupNoun}
		primary={isPrimary('compliance')}
		note="Compliance per app = mean over raters of met 1 · partial ½ · not met / blocked 0 (can't test excluded)."
	>
		<AgreementLine agreement={result.agreement} unit="(app, requirement) pairs" />
	</GroupScoreFigure>

	<StudyFigure id="tab-{result.block_id}-apps" kind="table" number="{section}.2" title="Compliance per app vs the conformance suite" flush>
		<div class="max-h-[28rem] overflow-auto">
			<table class="text-sm">
				<thead>
					<tr>
						<th>App</th>
						<th>{ctx.groupNoun}</th>
						<th class="num">Raters</th>
						<th class="num" title="Can't-test excluded">Compliance</th>
						<th class="num" title="Can't-test counted as 0">Strict</th>
						{#each sources as src (src)}<th class="num font-mono text-[11px]">{src}</th>{/each}
						<th class="num" title="Verdicts: blocked by a defect / couldn't test">Blocked · n/t</th>
						<th class="num" title="Automated conformance suite: all checks / the check categories matching the block's endpoint sources">Conformance · endpoints</th>
					</tr>
				</thead>
				<tbody>
					{#each result.per_app as a (a.app_id)}
						<tr>
							<td class="font-mono text-xs">{a.label}</td>
							<td class="max-w-48 truncate text-xs" title={ctx.groupLabel(a.group)}>{ctx.groupLabel(a.group)}</td>
							<td class="num text-xs">{a.n_raters}</td>
							<td class="num text-xs font-medium">{formatShare(a.compliance)}</td>
							<td class="num text-xs text-muted-foreground">{formatShare(a.compliance_strict)}</td>
							{#each sources as src (src)}<td class="num text-xs text-muted-foreground">{formatShare(a.by_source?.[src] ?? null)}</td>{/each}
							<td class="num text-xs text-muted-foreground">{a.blocked} · {a.cannot_test}</td>
							<td class="num text-xs">
								{formatShare(a.conformance_pass_rate)} · {formatShare(a.conformance_endpoint_pass_rate)}
								{#if a.conformance_status}<span class="block text-[11px] text-muted-foreground">{a.conformance_status}</span>{/if}
							</td>
						</tr>
					{:else}
						<tr><td colspan="8" class="text-xs text-muted-foreground">No rated apps.</td></tr>
					{/each}
				</tbody>
			</table>
		</div>
	</StudyFigure>

	<div class="grid gap-4 lg:grid-cols-2">
		<StudyFigure id="tab-{result.block_id}-validity" kind="table" number="{section}.3" title="Human compliance vs automated conformance" flush>
			<div class="overflow-x-auto">
				<table class="text-sm">
					<thead>
						<tr><th>Scope</th><th class="num normal-case">Spearman ρ [95% CI]</th><th class="num">Apps</th></tr>
					</thead>
					<tbody>
						{#each result.vs_conformance as v (v.scope)}
							<tr>
								<td>
									{v.scope === 'endpoints' ? 'Endpoints (primary)' : 'Overall (secondary)'}
									{#if v.note}<span class="block text-[11px] text-muted-foreground">{v.note}</span>{/if}
									{#if v.excluded_apps.length}<span class="block text-[11px] text-muted-foreground">Excluded: {v.excluded_apps.join(', ')}</span>{/if}
								</td>
								<td class="num text-xs"><span class="font-medium">{formatCoef(v.rho)}</span> <span class="text-muted-foreground">{formatCi(v.ci, coef)}</span></td>
								<td class="num text-xs">{v.n}</td>
							</tr>
						{:else}
							<tr><td colspan="3" class="text-xs text-muted-foreground">No apps with a conformance result.</td></tr>
						{/each}
					</tbody>
				</table>
			</div>
			{#snippet caption()}
				Convergent validity, not agreement: ranks within each app spec, restricted to the most common evaluator version. “Endpoints” compares
				the block's api / admin-api requirements with the matching check categories (api → api, admin-api → admin); “overall” compares different constructs.
			{/snippet}
		</StudyFigure>

		<StudyFigure id="tab-{result.block_id}-endpoints" kind="table" number="{section}.4" title="Endpoint verdicts vs conformance checks" flush>
			{#if ea}
				<div class="space-y-2 p-4">
					<table class="w-auto text-sm">
						<thead>
							<tr><th></th><th class="num">Check passed</th><th class="num">Check failed</th></tr>
						</thead>
						<tbody>
							<tr><th scope="row" class="text-left font-sans text-xs normal-case tracking-normal">Humans: met</th><td class="num">{ea.both_pass}</td><td class="num">{ea.human_only}</td></tr>
							<tr><th scope="row" class="text-left font-sans text-xs normal-case tracking-normal">Humans: not met</th><td class="num">{ea.auto_only}</td><td class="num">{ea.both_fail}</td></tr>
						</tbody>
					</table>
					<p class="text-xs text-muted-foreground">
						Cohen’s κ <span class="font-mono text-foreground">{formatCoef(ea.kappa)}</span> over {ea.n} matched endpoints;
						{ea.n_unmatched} unmatched (majority ties or no matching check).
					</p>
				</div>
			{:else}
				<p class="px-4 py-3 text-sm text-muted-foreground">No API requirements matched to conformance checks.</p>
			{/if}
			{#snippet caption()}Majority human verdict per (app, endpoint), partial counted as not met, matched by “METHOD /path”.{/snippet}
		</StudyFigure>
	</div>

	<details class="rounded-md border border-border bg-card">
		<summary class="cursor-pointer px-4 py-2.5 text-sm font-medium">Verdicts per requirement ({result.per_requirement.length})</summary>
		<div class="max-h-[28rem] overflow-auto border-t border-border/60">
			<table class="text-sm">
				<thead>
					<tr>
						<th>Requirement</th>
						<th>Source</th>
						<th class="num">n</th>
						<th class="num">Met</th>
						<th class="num">Partial</th>
						<th class="num">Not met</th>
						<th class="num">Blocked</th>
						<th class="num">Can't test</th>
					</tr>
				</thead>
				<tbody>
					{#each result.per_requirement as r (r.req_id)}
						<tr>
							<td class="max-w-md break-words text-xs">{r.text}</td>
							<td class="font-mono text-[11px] text-muted-foreground">{r.source}</td>
							<td class="num text-xs">{r.n_ratings}</td>
							<td class="num text-xs">{r.met}</td>
							<td class="num text-xs">{r.partial}</td>
							<td class="num text-xs">{r.not_met}</td>
							<td class="num text-xs">{r.blocked}</td>
							<td class="num text-xs text-muted-foreground">{r.cannot_test}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</details>
</div>
