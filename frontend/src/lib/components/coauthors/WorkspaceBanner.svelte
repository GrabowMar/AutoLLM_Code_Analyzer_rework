<script lang="ts">
	/**
	 * WorkspaceBanner — the strip under the app bar while a co-author works in
	 * someone else's workspace. Always visible (sticky, not dismissible). It
	 * names the owner and shows their e-mail, which, unlike a display name,
	 * cannot be made up. On phones there is room for one of the two, so the
	 * e-mail is shown. One action: back to the person's own account.
	 *
	 * Fixed height (h-10 = `--workspace-banner-h` in the app shell), so page
	 * level sticky elements can sit below it.
	 */
	import { toast } from 'svelte-sonner';
	import { Button } from '$lib/components/ui/button';
	import { formatApiError } from '$lib/api/core';
	import type { CoauthorWorkspace } from '$lib/api/coauthors';
	import { getWorkspace } from '$lib/stores/workspace.svelte';
	import Users from '@lucide/svelte/icons/users';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';

	let { workspace: ws }: { workspace: CoauthorWorkspace } = $props();

	const store = getWorkspace();
	let switching = $state(false);

	async function switchBack() {
		switching = true;
		try {
			await store.switchTo(null);
		} catch (err) {
			toast.error(formatApiError(err, 'Could not switch to your account.'));
			switching = false;
		}
	}
</script>

<div class="sticky top-[3rem] z-20 mt-12 h-10 border-b border-primary/25 bg-background" data-testid="workspace-banner">
	<div class="flex h-full items-center gap-2.5 bg-primary/10 px-3 text-sm sm:px-4 md:px-5">
		<Users class="size-4 shrink-0 text-primary" aria-hidden="true" />
		<p class="flex min-w-0 flex-1 items-baseline gap-2" role="status" title={ws.owner.email}>
			<span class="truncate">
				Working in <span class="font-semibold max-sm:hidden">{ws.owner.display_name}</span><span
					class="font-semibold sm:hidden">{ws.owner.email}</span
				>'s workspace
			</span>
			{#if ws.owner.display_name !== ws.owner.email}
				<span class="hidden min-w-0 shrink truncate text-xs text-muted-foreground sm:inline">{ws.owner.email}</span>
			{/if}
		</p>
		<Button
			variant="outline"
			size="sm"
			class="h-7 shrink-0 bg-background px-2.5"
			onclick={switchBack}
			disabled={switching}
			aria-busy={switching}
		>
			{#if switching}<LoaderCircle class="animate-spin motion-reduce:animate-none" aria-hidden="true" />{/if}
			<span class="hidden sm:inline">Switch to my account</span><span class="sm:hidden">My account</span>
		</Button>
	</div>
</div>
