<script lang="ts">
	import { Toaster as SonnerToaster } from 'svelte-sonner';
	import { mode } from 'mode-watcher';
</script>

<!-- Follow the app theme: without it toasts render light in dark mode. -->
<SonnerToaster theme={mode.current ?? 'system'} richColors closeButton position="bottom-right" />
