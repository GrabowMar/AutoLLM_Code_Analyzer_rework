<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import Layers from '@lucide/svelte/icons/layers';
	import History from '@lucide/svelte/icons/history';

	interface Props {
		result: { batch_id: string; job_count: number; status: string } | null;
		onViewHistory?: () => void;
	}

	let { result, onViewHistory }: Props = $props();
</script>

<Card.Root>
	<Card.Header class="pb-2">
		<Card.Title class="text-sm">Batch status</Card.Title>
	</Card.Header>
	<Card.Content>
		{#if result}
			<div class="space-y-3 text-sm">
				<div class="flex items-center gap-2 text-emerald-600 dark:text-emerald-400">
					<Layers class="h-4 w-4 shrink-0" />
					<span class="font-medium">{result.job_count} jobs queued</span>
				</div>
				<div class="space-y-1 text-xs text-muted-foreground">
					<div class="flex justify-between">
						<span>Batch ID</span>
						<span class="font-mono">{result.batch_id.slice(0, 8)}…</span>
					</div>
					<div class="flex justify-between">
						<span>Status</span>
						<span>{result.status}</span>
					</div>
				</div>
				{#if onViewHistory}
					<Button variant="outline" size="sm" class="w-full" onclick={onViewHistory}>
						<History class="mr-1.5 h-3.5 w-3.5" /> View in history
					</Button>
				{/if}
			</div>
		{:else}
			<div class="py-10 text-center text-sm text-muted-foreground">
				<Layers class="mx-auto h-10 w-10 opacity-30" />
				<p class="mt-3">Submit a batch to track progress here.</p>
			</div>
		{/if}
	</Card.Content>
</Card.Root>
