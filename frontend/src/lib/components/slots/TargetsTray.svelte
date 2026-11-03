<script lang="ts">
	/**
	 * TargetsTray — saved targets across the frozen parents (spec 10.5 step 3): validation status (pending /
	 * ok / uninformative with the dropped checks / error), leak score, the original run's result, "add the
	 * matching part from other parents" (same `match_key`), revalidate / delete, and "Create slot study"
	 * (`/studies/new?mode=slot&slot_sets=…&targets=…`, usable targets only). Pending targets are re-read every
	 * 10 s (each read lets the server settle a finished validation).
	 *
	 * Props: `sets` (frozen SlotSets with their targets), `onchanged()` (reload the sets), `compact`.
	 */
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import { Badge } from '$lib/components/ui/badge';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import Copy from '@lucide/svelte/icons/copy';
	import FlaskConical from '@lucide/svelte/icons/flask-conical';
	import RotateCw from '@lucide/svelte/icons/rotate-cw';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import { toast } from 'svelte-sonner';
	import { formatApiError } from '$lib/api/core';
	import { deleteSlotTarget, getSlotTarget, matchSlotTarget, revalidateSlotTarget } from '$lib/api/v3/slots';
	import type { SlotSetDetailOut, SlotTargetOut } from '$lib/api/v3/types';
	import { shortSha } from '$lib/components/runs/format';
	import { slotStudyHref, VALIDATION_TONES, validationLabel } from './validation';

	interface Props {
		sets: SlotSetDetailOut[];
		onchanged: () => void | Promise<void>;
	}

	let { sets, onchanged }: Props = $props();

	let busy = $state<string | null>(null);
	let confirmDelete = $state<SlotTargetOut | null>(null);
	let deleteOpen = $state(false);

	const targets = $derived(sets.flatMap((s) => s.targets.map((t) => ({ target: t, set: s }))));
	const pending = $derived(targets.filter(({ target }) => target.validation?.status === 'pending').map(({ target }) => target.id));
	const studyHref = $derived(slotStudyHref(sets.map((s) => s.id), targets.map(({ target }) => target)));
	const usableCount = $derived(targets.filter(({ target }) => target.usable).length);

	$effect(() => {
		if (pending.length === 0) return;
		const ids = pending;
		const timer = setInterval(async () => {
			const results = await Promise.allSettled(ids.map((id) => getSlotTarget(id)));
			if (results.some((r) => r.status === 'fulfilled' && r.value.validation?.status !== 'pending')) await onchanged();
		}, 10_000);
		return () => clearInterval(timer);
	});

	async function run(id: string, action: () => Promise<unknown>, done: string) {
		busy = id;
		try {
			await action();
			toast.success(done);
			await onchanged();
		} catch (e) {
			toast.error(formatApiError(e));
		} finally {
			busy = null;
		}
	}

	async function match(target: SlotTargetOut) {
		busy = target.id;
		try {
			// Prefer the parents frozen in this session (same task); otherwise every frozen parent of the task.
			const own = sets.find((s) => s.id === target.slot_set);
			const others = sets.filter((s) => s.id !== target.slot_set && s.task === own?.task).map((s) => s.id);
			const out = await matchSlotTarget(target.id, others.length ? others : 'same_task');
			const unmatched = out.unmatched.length ? `; ${out.unmatched.length} parent(s) have no matching part` : '';
			toast.success(`${out.created.length} matching target(s) created${unmatched}`);
			await onchanged();
		} catch (e) {
			toast.error(formatApiError(e, 'Could not match the part in other parents.'));
		} finally {
			busy = null;
		}
	}
</script>

<Card.Root class="gap-0 py-0">
	<Card.Header class="border-b border-border py-3">
		<Card.Title class="flex flex-wrap items-center gap-2 font-display">
			Targets
			<Badge variant="outline">{targets.length}</Badge>
			{#if pending.length}<Badge variant="secondary">{pending.length} validating</Badge>{/if}
		</Card.Title>
		<Card.Description>Usable in a study once validation is ok or uninformative (uninformative: warn in exploratory, error in confirmatory).</Card.Description>
	</Card.Header>
	<Card.Content class="space-y-2 py-3">
		{#if targets.length === 0}
			<p class="text-sm text-muted-foreground">No target yet. Select a part and save it.</p>
		{/if}
		<ul class="space-y-2">
			{#each targets as { target, set } (target.id)}
				<li class="rounded-md border border-border p-2.5 text-xs">
					<div class="flex flex-wrap items-center gap-2">
						<a class="font-medium text-primary hover:underline" href={`/slots/targets/${target.id}`}>{target.label || shortSha(target.id, 8)}</a>
						<Badge variant="outline">{target.kind}</Badge>
						<StatusBadge status={target.validation?.status ?? 'pending'} tones={VALIDATION_TONES} label={validationLabel(target)} />
						{#if target.leak?.matches?.some((m) => m.verbatim)}
							<StatusBadge status="error" label="verbatim leak" />
						{:else if (target.leak?.score ?? 0) > 0.3}
							<StatusBadge status="warning" label={`leak ${target.leak.score.toFixed(2)}`} />
						{/if}
					</div>
					<p class="mt-1 text-muted-foreground">
						{set.name || shortSha(set.id, 8)} · {set.parent_model} · <span class="font-mono">{target.address?.path}</span>
						{#if target.original_run}· original <a class="font-mono text-primary hover:underline" href={`/samples/${target.original_run}`}>{shortSha(target.original_run, 8)}</a>{/if}
					</p>
					{#if target.validation?.status === 'uninformative' || (target.validation?.dropped_checks?.length ?? 0) > 0}
						<p class="mt-1">
							Dropped checks: <span class="font-mono">{target.validation.dropped_checks.join(', ') || '—'}</span>
							{#if target.validation.flaky_checks.length}· flaky <span class="font-mono">{target.validation.flaky_checks.join(', ')}</span>{/if}
						</p>
					{/if}
					{#if target.validation?.status === 'ok'}
						<p class="mt-1 text-muted-foreground">{target.validation.discriminating_checks.length} discriminating check{target.validation.discriminating_checks.length === 1 ? '' : 's'}</p>
					{/if}
					{#if target.validation?.error}<p class="mt-1 text-danger-fg">{target.validation.error}</p>{/if}
					<div class="mt-1.5 flex flex-wrap gap-1">
						{#if target.match_key}
							<Button variant="ghost" size="sm" class="h-7" disabled={busy === target.id} onclick={() => match(target)} title="Create the same part (same endpoint / component) in the other frozen parents of this task">
								<Copy class="mr-1 h-3.5 w-3.5" />Add matching part from other parents
							</Button>
						{/if}
						{#if target.validation?.status === 'error' || target.validation?.status === 'uninformative'}
							<Button variant="ghost" size="sm" class="h-7" disabled={busy === target.id} onclick={() => run(target.id, () => revalidateSlotTarget(target.id), 'Validation queued again')}>
								<RotateCw class="mr-1 h-3.5 w-3.5" />Revalidate
							</Button>
						{/if}
						<Button variant="ghost" size="sm" class="h-7" disabled={busy === target.id} onclick={() => {
								confirmDelete = target;
								deleteOpen = true;
							}}>
							<Trash2 class="mr-1 h-3.5 w-3.5" />Delete
						</Button>
					</div>
				</li>
			{/each}
		</ul>
		<div class="flex flex-wrap items-center gap-2 border-t border-border pt-2">
			<span class="text-xs text-muted-foreground">{usableCount} usable of {targets.length}</span>
			<Button size="sm" class="ml-auto" href={usableCount ? studyHref : undefined} disabled={usableCount === 0} title={usableCount ? 'Prefilled slot study with these parents and targets' : 'Wait for at least one target to validate'}>
				<FlaskConical class="mr-1.5 h-3.5 w-3.5" />Create slot study
			</Button>
		</div>
	</Card.Content>
</Card.Root>

<ConfirmDialog
	bind:open={deleteOpen}
	title="Delete this target?"
	description="Only targets no study uses can be deleted; its validation runs are removed with it."
	confirmLabel="Delete"
	destructive
	busy={busy !== null}
	onConfirm={() => {
		const t = confirmDelete;
		deleteOpen = false;
		confirmDelete = null;
		if (t) void run(t.id, () => deleteSlotTarget(t.id), 'Target deleted');
	}}
/>
