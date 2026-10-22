<script lang="ts">
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import { Alert, AlertDescription } from '$lib/components/ui/alert';
	import { Button } from '$lib/components/ui/button';

	interface Props {
		message: string;
		title?: string;
		onretry?: () => void;
		class?: string;
	}

	let { message, title, onretry, class: className }: Props = $props();
</script>

<Alert variant="destructive" class={className}>
	<CircleAlert />
	<div class="flex flex-wrap items-start justify-between gap-2">
		<div class="min-w-0">
			{#if title}<p class="font-medium">{title}</p>{/if}
			<AlertDescription class="break-words">{message}</AlertDescription>
		</div>
		{#if onretry}
			<Button variant="outline" size="sm" onclick={onretry}>
				<RefreshCw />
				Retry
			</Button>
		{/if}
	</div>
</Alert>
