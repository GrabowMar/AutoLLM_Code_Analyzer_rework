<script lang="ts">
	import Sun from '@lucide/svelte/icons/sun';
	import Moon from '@lucide/svelte/icons/moon';
	import { toggleMode, mode } from 'mode-watcher';
</script>

<button
	onclick={toggleMode}
	class="inline-flex h-8 w-8 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground cursor-pointer"
	aria-label="Toggle theme"
>
	{#if mode.current === 'dark'}
		<Sun class="h-3.5 w-3.5" />
	{:else}
		<Moon class="h-3.5 w-3.5" />
	{/if}
</button>
