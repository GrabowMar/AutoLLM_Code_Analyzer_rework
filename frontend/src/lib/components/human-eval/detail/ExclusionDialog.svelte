<script lang="ts">
	/**
	 * ExclusionDialog — exclude a participant or an assignment from analysis
	 * with a reason (required), or include it again. Rows are kept; exports
	 * still carry them with the exclusion columns. Manual exclusions after the
	 * task opened are post-hoc: the results then stop being confirmatory.
	 *
	 * Props: `open` (bindable), `subject` ("P003", "this assignment"),
	 * `excluded` (current state), `reason` (current reason), `postHoc`
	 * (the task has opened), `onsubmit(excluded, reason)` (throws to keep the dialog open).
	 */
	import * as Dialog from '$lib/components/ui/dialog';
	import { Button } from '$lib/components/ui/button';
	import { Label } from '$lib/components/ui/label';
	import { Textarea } from '$lib/components/ui/textarea';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';

	interface Props {
		open: boolean;
		subject: string;
		excluded: boolean;
		reason?: string;
		postHoc?: boolean;
		onsubmit: (excluded: boolean, reason: string) => Promise<void>;
	}

	let { open = $bindable(false), subject, excluded, reason = '', postHoc = false, onsubmit }: Props = $props();

	let text = $state('');
	let saving = $state(false);
	let error = $state('');

	$effect(() => {
		if (open) {
			text = excluded ? reason : '';
			error = '';
		}
	});

	async function submit(e: SubmitEvent) {
		e.preventDefault();
		const next = !excluded;
		if (next && !text.trim()) {
			error = 'Give a reason; it is exported with the data.';
			return;
		}
		saving = true;
		error = '';
		try {
			await onsubmit(next, next ? text.trim() : '');
			open = false;
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
		} finally {
			saving = false;
		}
	}
</script>

<Dialog.Root bind:open>
	<Dialog.Content class="max-w-md">
		<Dialog.Header>
			<Dialog.Title>{excluded ? `Include ${subject} again?` : `Exclude ${subject} from analysis?`}</Dialog.Title>
			<Dialog.Description>
				{#if excluded}
					Currently excluded{reason ? `: “${reason}”` : ''}. Including it puts its submissions back into the results.
				{:else}
					Its rows are kept and exported (with the reason), but results leave them out unless you show excluded data.
				{/if}
			</Dialog.Description>
		</Dialog.Header>
		<form class="space-y-4" onsubmit={submit}>
			{#if !excluded}
				<div class="space-y-1.5">
					<Label for="he-exclusion-reason">Reason</Label>
					<Textarea id="he-exclusion-reason" rows={3} bind:value={text} placeholder="e.g. Did not follow the instructions; finished in 40 s" />
				</div>
			{/if}
			{#if postHoc && !excluded}
				<p class="text-xs text-muted-foreground">
					The task is already open: a manual exclusion now is a post-hoc decision, so the results are reported as exploratory
					(“post-hoc exclusions”). Pre-registered exclusion rules are applied automatically and need no manual exclusion.
				</p>
			{/if}
			{#if error}<p class="text-sm text-danger-fg" role="alert">{error}</p>{/if}
			<Dialog.Footer>
				<Button variant="outline" size="sm" type="button" disabled={saving} onclick={() => (open = false)}>Cancel</Button>
				<Button size="sm" type="submit" variant={excluded ? 'default' : 'destructive'} disabled={saving} aria-busy={saving}>
					{#if saving}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{/if}
					{excluded ? 'Include' : 'Exclude'}
				</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>
