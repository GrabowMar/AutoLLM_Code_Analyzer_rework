<script lang="ts">
	/**
	 * SpecPanel — what the app was asked to do (the frozen spec snapshot, when
	 * the study shows it) and the admin login when the study provides one.
	 * Everything is rendered as text.
	 *
	 * Props: `app` (PortalApp).
	 */
	import { toast } from 'svelte-sonner';
	import Copy from '@lucide/svelte/icons/copy';
	import KeyRound from '@lucide/svelte/icons/key-round';
	import type { PortalApp } from '$lib/api/types/human-eval';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import SectionHeading from '$lib/components/SectionHeading.svelte';
	import { Button } from '$lib/components/ui/button';

	interface Props {
		app: PortalApp;
	}

	let { app }: Props = $props();

	const spec = $derived(app.spec);
	const lists = $derived(
		spec
			? [
					{ title: 'Frontend', items: spec.frontend_requirements ?? [] },
				].filter((l) => l.items.length > 0)
			: []
	);
	const endpoints = $derived(
		spec
			? [
					{ title: 'API endpoints', items: spec.api_endpoints ?? [] },
					{ title: 'Admin API endpoints', items: spec.admin_api_endpoints ?? [] },
				].filter((l) => l.items.length > 0)
			: []
	);

	async function copy(text: string, what: string) {
		try {
			await navigator.clipboard.writeText(text);
			toast.success(`${what} copied`);
		} catch {
			toast.error('Could not copy to the clipboard');
		}
	}
</script>

<div class="space-y-5">
	{#if app.admin_credentials}
		<section class="rounded-md border border-border bg-surface-2 px-3 py-3">
			<p class="flex items-center gap-1.5 text-sm font-medium">
				<KeyRound class="size-4 text-muted-foreground" aria-hidden="true" />
				Admin login for {app.label}
			</p>
			<p class="mt-1 text-xs text-muted-foreground">
				Provided by the study platform. Weak or default admin credentials are not a finding of this app.
			</p>
			<dl class="mt-2 grid grid-cols-[auto_1fr_auto] items-center gap-x-3 gap-y-1 text-sm">
				<dt class="text-xs text-muted-foreground">Username</dt>
				<dd class="min-w-0 truncate font-mono">{app.admin_credentials.username}</dd>
				<Button variant="ghost" size="icon" class="size-7" aria-label="Copy username" onclick={() => copy(app.admin_credentials!.username, 'Username')}>
					<Copy class="size-3.5" />
				</Button>
				<dt class="text-xs text-muted-foreground">Password</dt>
				<dd class="min-w-0 truncate font-mono">{app.admin_credentials.password}</dd>
				<Button variant="ghost" size="icon" class="size-7" aria-label="Copy password" onclick={() => copy(app.admin_credentials!.password, 'Password')}>
					<Copy class="size-3.5" />
				</Button>
			</dl>
		</section>
	{:else if app.admin_credentials_status === 'not_measured'}
		<p class="flex items-center gap-1.5 rounded-md border border-border bg-surface-2 px-3 py-2 text-sm text-muted-foreground">
			<KeyRound class="size-4" aria-hidden="true" />
			Admin login not available for {app.label}.
		</p>
	{/if}

	{#if spec}
		<section class="space-y-2">
			<h3 class="font-display text-lg font-semibold">{spec.name}</h3>
			{#if spec.description}
				<p class="whitespace-pre-line text-sm leading-relaxed text-muted-foreground">{spec.description}</p>
			{/if}
		</section>
		{#if spec.prose}
			<section class="space-y-2">
				<SectionHeading size="sm" level={3} title="Brief given to the model" />
				<p class="whitespace-pre-line break-words text-sm leading-relaxed">{spec.prose}</p>
			</section>
		{/if}
		{#each lists as list (list.title)}
			<section class="space-y-2">
				<SectionHeading size="sm" level={3} title={list.title} count={list.items.length} />
				<ul class="list-disc space-y-1 pl-5 text-sm">
					{#each list.items as item, i (i)}
						<li class="whitespace-pre-line break-words">{item}</li>
					{/each}
				</ul>
			</section>
		{/each}
		{#each endpoints as group (group.title)}
			<section class="space-y-2">
				<SectionHeading size="sm" level={3} title={group.title} count={group.items.length} />
				<div class="overflow-x-auto rounded-md border border-border">
					<table class="w-full text-sm">
						<thead>
							<tr>
								<th class="w-20">Method</th>
								<th>Path</th>
								<th>Access</th>
								<th>Description</th>
							</tr>
						</thead>
						<tbody>
							{#each group.items as ep, i (i)}
								<tr>
									<td class="font-mono text-xs font-medium">{ep.method}</td>
									<td class="break-all font-mono text-xs">{ep.path}</td>
									<td class="text-xs text-muted-foreground">{ep.auth || '—'}</td>
									<td class="text-xs">{ep.description}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</section>
		{/each}
	{:else if !app.admin_credentials && app.admin_credentials_status !== 'not_measured'}
		<EmptyState compact title="No specification shown" description="This study does not show the app's specification." />
	{/if}
</div>
