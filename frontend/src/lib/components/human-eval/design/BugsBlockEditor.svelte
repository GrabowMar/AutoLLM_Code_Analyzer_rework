<script lang="ts">
	/**
	 * BugsBlockEditor — the minimum number of bug reports per app.
	 *
	 * Props: `id`, `block` (bindable), `mode` (pairwise counts per app),
	 * `errors` (relative to the block), `readOnly`.
	 */
	import NumberField from '$lib/components/studies/shared/NumberField.svelte';
	import type { BugsBlock, TaskMode } from '$lib/api/types/human-eval';
	import { messageAt, messagesAt, type FieldError } from './errors';

	interface Props {
		id: string;
		block: BugsBlock;
		mode: TaskMode;
		errors?: FieldError[];
		readOnly?: boolean;
	}

	let { id, block = $bindable(), mode, errors = [], readOnly = false }: Props = $props();
</script>

<div class="space-y-2">
	<div class="sm:w-64">
		<NumberField
			id="{id}-min-reports"
			label="Minimum bug reports per app"
			bind:value={() => block.min_reports, (v) => { if (v != null) block.min_reports = v; }}
			min={0}
			max={20}
			required
			disabled={readOnly}
			error={messageAt(errors, 'min_reports')}
		/>
	</div>
	<p class="text-[11px] text-muted-foreground">
		{block.min_reports === 0
			? `0: participants file any bugs they find, or confirm “no bugs found”${mode === 'pairwise' ? ' for each of the two apps' : ''}.`
			: `Participants must file at least ${block.min_reports} report${block.min_reports === 1 ? '' : 's'}${mode === 'pairwise' ? ' for each of the two apps' : ''} before submitting.`}
		Reports carry steps, location, the participant's own severity and screenshots.
	</p>
	{#each messagesAt(errors) as message (message)}
		<p class="text-[11px] text-danger-fg">{message}</p>
	{/each}
</div>
