<script lang="ts">
	/**
	 * PilotGateCard — a piloting study's gates (spec 10.3 §6, 4.6): each gate with its value and threshold,
	 * the cap hits by kind (with "raise run cap → re-pilot": edit the Budget preset and run a new pilot,
	 * whose plan is never pooled with this one), and Expand (pilot → main), optionally overriding failed
	 * gates with a recorded reason.
	 *
	 * Props: `studyId`, `onexpanded(result)`, `disabled`.
	 */
	import { expandStudy } from '$lib/api/v3/studies';
	import type { ExpandOut, PilotGate } from '$lib/api/v3/types';
	import { formatApiError } from '$lib/api/core';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Input } from '$lib/components/ui/input';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';

	interface Props {
		studyId: string;
		onexpanded: (result: ExpandOut) => void;
		disabled?: boolean;
	}

	let { studyId, onexpanded, disabled = false }: Props = $props();

	let gates = $state<PilotGate[] | null>(null);
	let override = $state(false);
	let reason = $state('');
	let busy = $state(false);
	let error = $state('');

	const failed = $derived((gates ?? []).filter((g) => !g.ok));
	const capHits = $derived((gates ?? []).filter((g) => g.code.includes('cap_hit') || g.code.includes('cap_binding')));

	async function expand() {
		busy = true;
		error = '';
		try {
			const out = await expandStudy(studyId, { override_gates: override, reason: override ? reason.trim() || null : null });
			gates = out.gates;
			if (out.created_runs > 0 || out.reused_pilot_cells > 0) onexpanded(out);
		} catch (e) {
			const body = e as { gates?: PilotGate[] };
			if (Array.isArray(body?.gates)) gates = body.gates;
			error = formatApiError(e, 'The pilot could not be expanded.');
		} finally {
			busy = false;
		}
	}

	const fmt = (v: number | null) => (v === null ? '—' : Math.abs(v) < 1 ? `${(v * 100).toFixed(1)}%` : v.toFixed(2));
</script>

<Card.Root>
	<Card.Header>
		<Card.Title>Pilot gates</Card.Title>
		<Card.Description>The main stage starts once the pilot passes every gate (or you override them, with a reason).</Card.Description>
	</Card.Header>
	<Card.Content class="space-y-3">
		{#if gates}
			<table class="w-full text-xs">
				<thead><tr><th>Gate</th><th class="num">Value</th><th class="num">Threshold</th><th>Result</th></tr></thead>
				<tbody>
					{#each gates as g (g.code)}
						<tr>
							<td class="font-mono">{g.code}</td>
							<td class="num">{fmt(g.value)}</td>
							<td class="num">{fmt(g.threshold)}</td>
							<td><StatusBadge status={g.ok ? 'passed' : 'failed'} tone={g.ok ? 'success' : 'warning'} label={g.ok ? 'pass' : 'fail'} /></td>
						</tr>
					{/each}
				</tbody>
			</table>
			{#if capHits.some((g) => !g.ok)}
				<p class="text-xs text-warning-fg">
					Runs hit their caps: raise the run cap in the Budget preset and re-pilot (<a class="underline" href="/experiments/new?clone={studyId}">clone and edit</a>). Pilots with a different plan are never pooled.
				</p>
			{/if}
		{:else}
			<p class="text-xs text-muted-foreground">The gates are evaluated when you expand.</p>
		{/if}
		{#if failed.length > 0}
			<label class="flex items-center gap-2 text-xs">
				<Checkbox bind:checked={override} {disabled} />
				Override {failed.length} failed gate{failed.length === 1 ? '' : 's'}
			</label>
			{#if override}
				<Input class="h-8 text-sm" placeholder="Reason (recorded with the experiment)" bind:value={reason} {disabled} />
			{/if}
		{/if}
		{#if error}<ErrorAlert message={error} />{/if}
		<Button size="sm" onclick={expand} disabled={disabled || busy || (override && !reason.trim())}>
			{#if busy}<LoaderCircle class="animate-spin" />{/if}Expand to the main stage
		</Button>
	</Card.Content>
</Card.Root>
