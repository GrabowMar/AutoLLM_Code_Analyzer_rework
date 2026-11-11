<script lang="ts">
	/**
	 * Confirm a draft selection snapshot (staff): keep each nominal pick or swap it
	 * for another eligible model of its tier, with a reason for every swap, plus
	 * notes (e.g. how a borderline pick was decided). The backend records the
	 * confirmation and supersedes any earlier confirmed snapshot.
	 *
	 * Props: `open` (bindable), `snapshot` (SelectionSnapshotDetail, status draft),
	 * `onconfirmed(detail)`.
	 */
	import * as Dialog from '$lib/components/ui/dialog';
	import * as Select from '$lib/components/ui/select';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Textarea } from '$lib/components/ui/textarea';
	import { Label } from '$lib/components/ui/label';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import { formatApiError } from '$lib/api/core';
	import { confirmSelection, type SelectionSnapshotDetail } from '$lib/api/rankings';
	import { candidateIndex, confirmBody, fmtPct, robustnessTone, shortName, tierLabel, type SwapChoice } from './mss';

	interface Props {
		open: boolean;
		snapshot: SelectionSnapshotDetail;
		onconfirmed: (detail: SelectionSnapshotDetail) => void;
	}

	let { open = $bindable(false), snapshot, onconfirmed }: Props = $props();

	const payload = $derived(snapshot.payload);
	const picks = $derived(payload.selection?.picks ?? []);
	const cands = $derived(candidateIndex(payload));
	const nominal = $derived(picks.map((p) => p.canonical_id));

	let swaps = $state<Record<string, SwapChoice>>({});
	let notes = $state('');
	let busy = $state(false);
	let error = $state('');
	let tried = $state(false);

	// A new snapshot starts from its nominal picks.
	$effect(() => {
		void snapshot.id;
		swaps = {};
		notes = '';
		error = '';
		tried = false;
	});

	/** Swap options for one pick: its precomputed replacement first, then the other eligible models of its tier by MSS_tier. */
	function options(pickId: string, tier: string): Select.SimpleSelectItem[] {
		const taken = new Set(nominal);
		const repl = (payload.alternates?.replacements ?? []).find((r) => r.replace === pickId);
		const first = (repl?.with ?? []).filter((id) => !taken.has(id));
		const rest = (payload.candidates ?? [])
			.filter((c) => c.eligible && c.tier === tier && !taken.has(c.canonical_id) && !first.includes(c.canonical_id))
			.sort((a, b) => (b.mss_tier ?? -1) - (a.mss_tier ?? -1))
			.map((c) => c.canonical_id);
		return [...first, ...rest].map((id) => {
			const c = cands.get(id);
			const bits = [
				first.includes(id) ? 'enters when this pick is left out' : null,
				c?.mss_tier != null ? `MSS_tier ${(c.mss_tier * 100).toFixed(1)}` : null,
				c?.admissible === false ? (c.floor_reason ?? 'not admissible') : null,
			].filter(Boolean);
			return { value: id, label: shortName(id, cands), description: bits.join(' · ') };
		});
	}

	function setSwap(id: string, patch: Partial<SwapChoice>) {
		const cur = swaps[id] ?? { with: '', reason: '' };
		swaps = { ...swaps, [id]: { ...cur, ...patch } };
	}

	const body = $derived(confirmBody(nominal, swaps));
	const changed = $derived(body.overrides.length);

	async function submit() {
		tried = true;
		error = '';
		if (body.problems.length) return;
		busy = true;
		try {
			const detail = await confirmSelection(snapshot.id, {
				final_ids: body.final_ids,
				overrides: body.overrides,
				notes: notes.trim(),
			});
			open = false;
			onconfirmed(detail);
		} catch (e) {
			error = formatApiError(e, 'Could not confirm the selection.');
		} finally {
			busy = false;
		}
	}
</script>

<Dialog.Root bind:open>
	<Dialog.Content class="max-h-[90vh] max-w-2xl overflow-y-auto">
		<Dialog.Header>
			<Dialog.Title>Confirm study selection</Dialog.Title>
			<Dialog.Description>
				Snapshot #{snapshot.id} · as of {snapshot.as_of ?? '—'}. Keep each pick or swap it for another model of its tier; every swap needs a reason. Generation uses the confirmed list.
			</Dialog.Description>
		</Dialog.Header>

		<ul class="divide-y divide-border rounded-md border border-border">
			{#each picks as p (p.canonical_id)}
				{@const swap = swaps[p.canonical_id]}
				{@const opts = options(p.canonical_id, p.tier)}
				<li class="space-y-2 px-3 py-2.5">
					<div class="flex flex-wrap items-center gap-2">
						<span class="w-16 shrink-0 text-[11px] text-muted-foreground">{tierLabel(p.tier)} #{p.tier_rank}</span>
						<span class="min-w-0 flex-1 truncate text-sm font-medium {swap?.with ? 'text-muted-foreground line-through' : ''}" title={p.canonical_id}>{shortName(p.canonical_id, cands)}</span>
						{#if p.robustness}
							<StatusBadge status={p.robustness} tone={robustnessTone(p.robustness)} label="{p.robustness} {fmtPct(p.selection_frequency)}" />
						{/if}
						<Select.Simple
							size="sm"
							class="w-full sm:w-56"
							aria-label="Replace {shortName(p.canonical_id, cands)}"
							value={swap?.with ?? ''}
							items={opts}
							placeholder="Keep"
							clearLabel="Keep"
							disabled={busy || opts.length === 0}
							onValueChange={(v) => setSwap(p.canonical_id, { with: v })}
						/>
					</div>
					{#if swap?.with}
						<div class="space-y-1 pl-0 sm:pl-[4.5rem]">
							<Label for="reason-{p.position}" class="text-xs">Reason for the swap</Label>
							<Input
								id="reason-{p.position}"
								value={swap.reason}
								oninput={(e) => setSwap(p.canonical_id, { reason: (e.target as HTMLInputElement).value })}
								placeholder="e.g. borderline in S1; the contender is more widely used"
								aria-invalid={tried && !swap.reason.trim()}
								disabled={busy}
							/>
						</div>
					{/if}
				</li>
			{/each}
		</ul>

		<div class="space-y-1">
			<Label for="confirm-notes" class="text-xs">Notes</Label>
			<Textarea id="confirm-notes" bind:value={notes} rows={3} placeholder="How borderline picks were decided, budget decisions, anything a reader of the Methods should know" disabled={busy} />
		</div>

		{#if tried && body.problems.length}
			<ul class="space-y-0.5 text-xs text-danger-fg" role="alert">
				{#each body.problems as msg (msg)}<li>{msg}</li>{/each}
			</ul>
		{/if}
		{#if error}<p class="text-xs text-danger-fg" role="alert">{error}</p>{/if}

		<Dialog.Footer>
			<span class="mr-auto self-center text-[11px] text-muted-foreground">{changed ? `${changed} swap${changed === 1 ? '' : 's'}` : 'Nominal picks unchanged'}</span>
			<Button variant="outline" size="sm" disabled={busy} onclick={() => (open = false)}>Cancel</Button>
			<Button size="sm" disabled={busy || picks.length === 0} onclick={submit}>
				{#if busy}<LoaderCircle class="mr-1.5 size-3.5 animate-spin motion-reduce:animate-none" />{/if}
				Confirm {body.final_ids.length} models
			</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>
