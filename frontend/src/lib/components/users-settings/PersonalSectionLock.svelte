<script lang="ts">
	/**
	 * PersonalSectionLock — stands in for a settings section that belongs to the
	 * signed-in person's own account (profile, sign-in, tokens, co-authors…)
	 * while they work in someone else's workspace.
	 */
	import { toast } from 'svelte-sonner';
	import { Button } from '$lib/components/ui/button';
	import { formatApiError } from '$lib/api/core';
	import { getWorkspace } from '$lib/stores/workspace.svelte';
	import Lock from '@lucide/svelte/icons/lock';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';

	const workspace = getWorkspace();
	let switching = $state(false);

	async function switchBack() {
		switching = true;
		try {
			await workspace.switchTo(null);
		} catch (err) {
			toast.error(formatApiError(err, 'Could not switch to your account.'));
			switching = false;
		}
	}
</script>

<div class="settings-row flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between sm:gap-6">
	<p class="flex items-start gap-2.5 text-sm text-muted-foreground">
		<Lock class="mt-0.5 size-4 shrink-0" aria-hidden="true" />
		<span>This is part of your own account. Switch to your account to change it.</span>
	</p>
	<Button variant="outline" size="sm" class="shrink-0 self-start sm:self-auto" onclick={switchBack} disabled={switching} aria-busy={switching}>
		{#if switching}<LoaderCircle class="animate-spin motion-reduce:animate-none" aria-hidden="true" />{/if}
		Switch to my account
	</Button>
</div>
