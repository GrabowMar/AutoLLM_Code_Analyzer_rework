<script lang="ts">
	/**
	 * CoauthorActivityDialog — what one co-author changed in your workspace:
	 * every write they made (method, path, status), newest first, plus the
	 * attempts that were blocked. Reads are not recorded. "Show older" pages
	 * back with `before` = the smallest id shown.
	 */
	import { untrack } from 'svelte';
	import * as Dialog from '$lib/components/ui/dialog';
	import { Button } from '$lib/components/ui/button';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import { formatApiError } from '$lib/api/core';
	import { getMemberActivity, type CoauthorAction, type CoauthorMember } from '$lib/api/coauthors';
	import { formatAgo } from '$lib/components/inference/endpoint-display';
	import History from '@lucide/svelte/icons/history';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';

	const PAGE = 50;

	let { open = $bindable(false), member }: { open: boolean; member: CoauthorMember | null } = $props();

	let actions = $state<CoauthorAction[]>([]);
	let loading = $state(false);
	let loadingMore = $state(false);
	let error = $state('');
	let hasMore = $state(false);
	let loadedFor: number | null = null;
	let seq = 0;

	async function load(more = false) {
		if (!member) return;
		const id = member.coauthorship_id;
		const mine = ++seq;
		const before = more && actions.length ? actions[actions.length - 1].id : undefined;
		if (more) loadingMore = true;
		else loading = true;
		error = '';
		try {
			const page = await getMemberActivity(id, { limit: PAGE, before });
			if (mine !== seq) return;
			actions = more ? [...actions, ...page] : page;
			hasMore = page.length === PAGE;
		} catch (e) {
			if (mine !== seq) return;
			error = formatApiError(e, 'Could not load the activity.');
		} finally {
			if (mine === seq) {
				loading = false;
				loadingMore = false;
			}
		}
	}

	$effect(() => {
		const id = open ? (member?.coauthorship_id ?? null) : null;
		untrack(() => {
			if (id === null) {
				loadedFor = null;
				return;
			}
			if (id === loadedFor) return;
			loadedFor = id;
			actions = [];
			hasMore = false;
			void load();
		});
	});

	const name = $derived(member?.member.display_name ?? '');
</script>

<Dialog.Root bind:open>
	<Dialog.Content class="flex max-h-[85dvh] w-[calc(100%-2rem)] max-w-xl flex-col gap-4">
		<Dialog.Header>
			<Dialog.Title class="pr-6 break-words">Activity of {name}</Dialog.Title>
			<Dialog.Description>
				Changes they made in your workspace, newest first. Blocked attempts are included. Viewing pages is not
				recorded.
			</Dialog.Description>
		</Dialog.Header>

		<div class="-mx-6 min-h-0 flex-1 overflow-y-auto border-y border-border">
			{#if error && !actions.length}
				<div class="p-4"><ErrorAlert message={error} onretry={() => void load()} /></div>
			{:else if loading}
				<LoadingState label="loading activity" class="py-10" />
			{:else if actions.length === 0}
				<EmptyState compact icon={History} title="No changes recorded." />
			{:else}
				<ul class="divide-y divide-border">
					{#each actions as a (a.id)}
						<li class="flex items-start justify-between gap-3 px-6 py-2.5">
							<div class="min-w-0 space-y-0.5">
								<p class="font-mono text-xs break-all">
									<span class="font-medium">{a.method}</span>
									<span class="text-muted-foreground">{a.path}</span>
								</p>
								<p class="text-xs text-muted-foreground">
									<time datetime={a.created_at} title={new Date(a.created_at).toLocaleString()}>{formatAgo(a.created_at)}</time>
								</p>
							</div>
							<div class="flex shrink-0 items-center gap-2">
								{#if a.status_code === 403}
									<StatusBadge status="blocked" tone="danger" label="Blocked" />
								{/if}
								<span
									class="font-mono text-xs tabular-nums {a.status_code >= 400 ? 'text-danger-fg' : 'text-muted-foreground'}"
									title="HTTP status">{a.status_code}</span
								>
							</div>
						</li>
					{/each}
				</ul>
				{#if error}
					<div class="p-4"><ErrorAlert message={error} onretry={() => void load(true)} /></div>
				{:else if hasMore}
					<div class="flex justify-center border-t border-border px-6 py-2.5">
						<Button variant="ghost" size="sm" onclick={() => void load(true)} disabled={loadingMore} aria-busy={loadingMore}>
							{#if loadingMore}<LoaderCircle class="animate-spin motion-reduce:animate-none" aria-hidden="true" />{/if}
							Show older
						</Button>
					</div>
				{/if}
			{/if}
		</div>

		<Dialog.Footer>
			<Button variant="outline" size="sm" onclick={() => (open = false)}>Close</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>
