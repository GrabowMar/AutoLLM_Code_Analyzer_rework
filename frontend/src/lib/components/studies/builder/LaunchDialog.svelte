<script lang="ts">
	/**
	 * LaunchDialog — the cost confirmation before Start (spec 10.2, 4.4): expected / p90 / worst case
	 * against the cap, the cap resolved from `auto` (editable), what happens when the budget runs out,
	 * the pilot, and a typed confirmation when p90 exceeds the preset's confirmation threshold. The
	 * server refuses a launch whose `confirm_usd` is below p90, so the typed amount is what is sent.
	 *
	 * Props: `open`, `plan` (the frozen `PlanOut`), `policy` (the content of the Budget preset version the
	 * design PINS — not the slug's latest — for the threshold, cap, pilot and exhaustion rule; null while it
	 * loads, which holds Start), `policyRef`, `policyError`, `busy`, `error`, `onlaunch(body)`, `onclose()`.
	 * `study_cap` is sent only when the researcher changed the cap from the pinned preset's own value
	 * (`launchStudyCap`); otherwise the server applies the pinned preset's cap.
	 */
	import type { LaunchIn, PlanOut, ResourcePolicyContent } from '$lib/api/v3/types';
	import * as Dialog from '$lib/components/ui/dialog';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Segmented } from '$lib/components/ui/segmented';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import NumberField from '../shared/NumberField.svelte';
	import { formatSecondsRange, formatUsd, plural } from '../format';
	import { estimateBasisTotals, launchStudyCap } from './policy';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Play from '@lucide/svelte/icons/play';

	interface Props {
		open: boolean;
		plan: PlanOut | null;
		policy: ResourcePolicyContent | null;
		policyRef?: string;
		policyError?: string;
		busy?: boolean;
		error?: string;
		onlaunch: (body: Omit<LaunchIn, 'plan_hash'>) => void;
		onclose: () => void;
	}

	let { open, plan, policy, policyRef = '', policyError = '', busy = false, error = '', onlaunch, onclose }: Props = $props();

	const estimate = $derived(plan?.estimate ?? null);
	// Expected / p90 in the cap's unit: list price when the endpoint is not billed (E2E-13).
	const totals = $derived(estimate ? estimateBasisTotals(estimate) : null);
	const listBasis = $derived(totals?.basis === 'list');
	const threshold = $derived(policy?.estimate.require_confirm_above_usd ?? 0);
	const needsConfirm = $derived(!!totals && totals.p90 > threshold);
	const p90Text = $derived(totals ? totals.p90.toFixed(2) : '');
	const policyCap = $derived(policy?.study.max_cost_usd ?? 'auto');

	let capMode = $state<'auto' | 'fixed'>('auto');
	let cap = $state<number | null>(null);
	let typed = $state('');
	let pilotOverride = $state(false);

	$effect(() => {
		if (!open) return;
		typed = '';
		pilotOverride = false;
		capMode = policyCap === 'auto' ? 'auto' : 'fixed';
		cap = policyCap === 'auto' ? (estimate?.cap_usd ?? null) : Number(policyCap);
	});

	const confirmed = $derived(!needsConfirm || Number(typed.replace(/[$,\s]/g, '')) >= (totals?.p90 ?? 0) - 0.005);
	const capTooLow = $derived(capMode === 'fixed' && cap !== null && !!totals && cap < totals.expected);
	const EXHAUSTED: Record<string, string> = {
		stop_balanced: 'claim no new block; started blocks finish (balanced: no unit ends at full n while another has none)',
		pause: 'pause the study until you raise the cap',
		stop: 'cancel the pending runs and stop',
	};

	function launch() {
		if (!estimate || !totals || !policy) return;
		onlaunch({
			confirm_usd: needsConfirm ? Number(typed.replace(/[$,\s]/g, '')) : totals.p90,
			study_cap: launchStudyCap(policyCap, capMode, cap),
			pilot_override: pilotOverride,
		});
	}
</script>

<Dialog.Root {open} onOpenChange={(v) => !v && onclose()}>
	<Dialog.Content class="max-w-lg">
		<Dialog.Header>
			<Dialog.Title>Start the study</Dialog.Title>
			<Dialog.Description>
				{#if plan}{plural(plan.n_cells, 'cell')} in {plural(plan.n_blocks, 'block')}{#if plan.n_cells_by_stage.pilot} · the pilot runs first ({plural(plan.n_cells_by_stage.pilot, 'cell')}){/if}.{/if}
			</Dialog.Description>
		</Dialog.Header>
		{#if estimate}
			<div class="space-y-4">
				<dl class="grid grid-cols-3 gap-2 text-center">
					<div class="rounded-md border border-border px-2 py-1.5">
						<dt class="kpi-label">Expected</dt>
						<dd class="font-mono text-lg tabular-nums">{formatUsd(totals?.expected)}</dd>
					</div>
					<div class="rounded-md border border-border px-2 py-1.5">
						<dt class="kpi-label">p90</dt>
						<dd class="font-mono text-lg tabular-nums">{formatUsd(totals?.p90)}</dd>
					</div>
					<div class="rounded-md border border-border px-2 py-1.5">
						<dt class="kpi-label">Worst case</dt>
						<dd class="font-mono text-lg tabular-nums">{formatUsd(estimate.worst_case_total)}</dd>
					</div>
				</dl>
				<p class="text-xs text-muted-foreground">
					{#if listBasis}At list price: the endpoint bills {formatUsd(estimate.expected_total)}, and the cap counts list price.{:else}At list price ≈ {formatUsd(estimate.expected_list_total)}.{/if}
					{#if estimate.time}Wall time {formatSecondsRange(estimate.time.expected_wall_s_low, estimate.time.expected_wall_s_high)} at {estimate.time.effective_parallel} parallel.{/if}
					{#if estimate.fit.max_repeats_under_cap !== null}Fits {estimate.fit.max_repeats_under_cap} repeats under the cap.{/if}
					p90 is the sum of per-cell p90s (conservative).
				</p>

				<div class="space-y-2">
					<p class="text-sm font-medium">Study cap</p>
					<div class="flex flex-wrap items-end gap-3">
						<Segmented
							aria-label="Study cap"
							size="sm"
							value={capMode}
							items={[
								{ value: 'auto', label: estimate.cap_usd !== null ? `Auto (${formatUsd(estimate.cap_usd)})` : 'Auto' },
								{ value: 'fixed', label: 'Fixed' },
							]}
							onValueChange={(v) => (capMode = v as 'auto' | 'fixed')}
						/>
						{#if capMode === 'fixed'}
							<NumberField id="launch-cap" label="Cap (USD)" bind:value={cap} min={0} integer={false} step={0.5} class="w-36" />
						{/if}
					</div>
					{#if capTooLow}<p class="text-xs text-danger-fg">The cap is below the expected total; the study would stop early.</p>{/if}
					<p class="text-xs text-muted-foreground">
						When it is reached the scheduler will {EXHAUSTED[policy?.study.on_budget_exhausted ?? 'stop_balanced'] ?? policy?.study.on_budget_exhausted}.
					</p>
				</div>

				{#if policy?.pilot.enabled}
					<div class="space-y-1 text-xs">
						<p class="text-sm font-medium">Pilot</p>
						<p class="text-muted-foreground">
							{policy.pilot.repeats} repeat(s) on {policy.pilot.units === 'all' ? 'every unit' : `${policy.pilot.units} unit(s)`}{policy.pilot.smoke_first ? ', smoke cells first' : ''};
							the main stage starts {policy.pilot.auto_expand ? 'automatically when every gate passes' : 'when you expand it'}.
						</p>
						<label class="flex items-center gap-2">
							<Checkbox bind:checked={pilotOverride} />
							Skip the pilot this time (recorded)
						</label>
					</div>
				{/if}

				{#if needsConfirm}
					<div class="space-y-1.5">
						<Label for="launch-confirm">Type <span class="font-mono">{p90Text}</span> to confirm the p90 spend</Label>
						<Input id="launch-confirm" class="h-8 font-mono" inputmode="decimal" autocomplete="off" bind:value={typed} />
					</div>
				{/if}
				{#if policyError}
					<ErrorAlert message={policyError} />
				{:else if !policy}
					<p class="text-xs text-muted-foreground">Loading the pinned budget preset{policyRef ? ` ${policyRef}` : ''}…</p>
				{/if}
				{#if error}<ErrorAlert message={error} />{/if}
			</div>
		{:else}
			<p class="text-sm text-muted-foreground">The plan has no estimate yet.</p>
		{/if}
		<Dialog.Footer class="mt-4 flex justify-end gap-2">
			<Button variant="outline" onclick={onclose}>Cancel</Button>
			<Button onclick={launch} disabled={!estimate || !policy || !confirmed || busy || (capMode === 'fixed' && !cap)}>
				{#if busy}<LoaderCircle class="animate-spin" />{:else}<Play />{/if}Start
			</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>
