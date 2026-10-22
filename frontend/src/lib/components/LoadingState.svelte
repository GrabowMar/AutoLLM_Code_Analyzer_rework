<script lang="ts">
	import { cn } from '$lib/utils';

	interface Props {
		label?: string;
		class?: string;
	}

	let { label = 'loading', class: className }: Props = $props();
</script>

<div class={cn('flex items-center justify-center py-16', className)} role="status" aria-live="polite">
	<span class="loading-mono">{label}</span>
</div>
