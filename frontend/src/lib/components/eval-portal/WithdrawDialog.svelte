<script lang="ts">
	/**
	 * WithdrawDialog — confirm withdrawing from a study, with the option to delete
	 * everything the participant has given (assignments, answers, reports,
	 * screenshots, questionnaire answers).
	 *
	 * Props: `open` (bindable), `busy`, `error`, `onConfirm(deleteData)`.
	 */
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import * as Dialog from '$lib/components/ui/dialog';
	import { Button } from '$lib/components/ui/button';
	import { Checkbox } from '$lib/components/ui/checkbox';

	interface Props {
		open: boolean;
		busy?: boolean;
		error?: string;
		onConfirm: (deleteData: boolean) => void;
	}

	let { open = $bindable(false), busy = false, error = '', onConfirm }: Props = $props();

	const uid = $props.id();
	let deleteData = $state(false);

	$effect(() => {
		if (!open) deleteData = false;
	});
</script>

<Dialog.Root bind:open>
	<Dialog.Content class="max-w-md">
		<Dialog.Header>
			<Dialog.Title>Withdraw from this study?</Dialog.Title>
			<Dialog.Description class="text-sm text-muted-foreground">
				You stop taking part and cannot continue later. Taking part is voluntary, so you do not have to
				give a reason.
			</Dialog.Description>
		</Dialog.Header>
		<div class="space-y-2 py-2">
			<label class="flex cursor-pointer items-start gap-2.5 text-sm" for="withdraw-delete-{uid}">
				<Checkbox id="withdraw-delete-{uid}" class="mt-0.5" bind:checked={deleteData} disabled={busy} />
				<span>
					<span class="font-medium">Also delete everything I have given</span>
					<span class="mt-0.5 block text-xs text-muted-foreground">
						Your ratings, answers, reports and screenshots are removed. Without this, what you have already
						submitted stays in the study's pseudonymous data.
					</span>
				</span>
			</label>
			{#if error}
				<p class="text-sm font-medium text-danger-fg" role="alert">{error}</p>
			{/if}
		</div>
		<Dialog.Footer>
			<Button variant="outline" size="sm" disabled={busy} onclick={() => (open = false)}>Cancel</Button>
			<Button variant="destructive" size="sm" disabled={busy} onclick={() => onConfirm(deleteData)}>
				{#if busy}
					<LoaderCircle class="animate-spin motion-reduce:animate-none" aria-hidden="true" />
				{/if}
				{deleteData ? 'Withdraw and delete' : 'Withdraw'}
			</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>
