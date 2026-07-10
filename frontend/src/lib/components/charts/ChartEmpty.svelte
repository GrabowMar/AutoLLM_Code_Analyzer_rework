<script lang="ts">
	import ChartLine from '@lucide/svelte/icons/chart-line';

	interface Props {
		message?: string;
		height?: number;
	}

	let { message = 'Not enough data to chart yet.', height = 208 }: Props = $props();
</script>

<div
	class="flex w-full flex-col items-center justify-center gap-2 text-muted-foreground"
	style="height: {height}px"
>
	<ChartLine class="h-6 w-6 opacity-50" />
	<p class="text-xs">{message}</p>
</div>
