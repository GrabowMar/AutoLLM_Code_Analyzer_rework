<script lang="ts">
	import { page } from '$app/state';
	import { Button } from '$lib/components/ui/button';
	import FileSearch from '@lucide/svelte/icons/file-search';
	import ShieldAlert from '@lucide/svelte/icons/shield-alert';
	import ServerCrash from '@lucide/svelte/icons/server-crash';
	import PlugZap from '@lucide/svelte/icons/plug-zap';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';

	const statusInfo: Record<number, { title: string; message: string; icon: any }> = {
		404: { title: 'Page Not Found', message: "The page you're looking for doesn't exist or has been moved.", icon: FileSearch },
		403: { title: 'Access Denied', message: "You don't have permission to access this resource.", icon: ShieldAlert },
		500: { title: 'Internal Server Error', message: 'Something went wrong on our end. Please try again later.', icon: ServerCrash },
		503: { title: 'Service Unavailable', message: 'The service is temporarily unavailable. Please try again later.', icon: PlugZap }
	};

	const info = statusInfo[page.status] ?? { title: 'Something Went Wrong', message: 'An unexpected error occurred.', icon: TriangleAlert };
	const Icon = info.icon;
</script>

<svelte:head>
	<title>{page.status} {info.title} - LLM Lab</title>
</svelte:head>

<div class="flex min-h-[70vh] items-center justify-center p-4">
	<div class="w-full max-w-lg">
		<div class="terminal mb-4 text-xs">
			<span class="text-primary">$</span> cat /var/log/error.log <span class="text-muted-foreground">| tail -1</span>
		</div>
		<div class="rounded-md border border-border bg-card overflow-hidden">
			<div class="flex items-center gap-2 border-b border-border bg-surface-2 px-4 py-2 text-xs" style="font-family: var(--font-mono);">
				<span class="h-2.5 w-2.5 rounded-full bg-destructive/70"></span>
				<span class="h-2.5 w-2.5 rounded-full bg-amber-500/60"></span>
				<span class="h-2.5 w-2.5 rounded-full bg-emerald-500/60"></span>
				<span class="ml-2 text-muted-foreground">error_handler.ts — line {page.status}</span>
			</div>
			<div class="px-6 py-8 text-center">
				<div class="mb-5 flex justify-center">
					<div class="flex h-16 w-16 items-center justify-center rounded-md bg-destructive/10 text-destructive">
						<Icon class="h-8 w-8" />
					</div>
				</div>
				<div class="text-5xl font-bold tabular-nums tracking-tight" style="font-family: var(--font-display);">
					{page.status}
				</div>
				<div class="mt-1 text-sm font-medium text-foreground" style="font-family: var(--font-display);">
					{info.title}
				</div>
				<p class="mt-3 text-sm text-muted-foreground">
					{page.error?.message || info.message}
				</p>
				<div class="mt-6 flex justify-center gap-3">
					<Button href="/">Go to Dashboard</Button>
					<Button variant="outline" onclick={() => history.back()}>Go Back</Button>
				</div>
				<p class="mt-6 text-[10px] uppercase tracking-wider text-muted-foreground" style="font-family: var(--font-mono);">
					// error_code: {page.status}
				</p>
			</div>
		</div>
	</div>
</div>
