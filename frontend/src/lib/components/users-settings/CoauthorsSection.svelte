<script lang="ts">
	/**
	 * CoauthorsSection — people who work in your workspace (activity, remove),
	 * invite links (create, copy once, revoke), and the workspaces other people
	 * invited you to (open, leave). Owner-only: the settings page shows
	 * `PersonalSectionLock` instead while you work in someone else's workspace.
	 */
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import {
		createInvite,
		leaveWorkspace,
		listInvites,
		listMembers,
		removeMember,
		revokeInvite,
		type CoauthorInvite,
		type CoauthorInviteCreated,
		type CoauthorMember,
		type CoauthorWorkspace,
		type InviteStatus,
	} from '$lib/api/coauthors';
	import { formatApiError } from '$lib/api/core';
	import { getMe } from '$lib/api/users';
	import { getWorkspace } from '$lib/stores/workspace.svelte';
	import type { Tone } from '$lib/constants/colors';
	import SettingsBlock from './SettingsBlock.svelte';
	import CopyField from './CopyField.svelte';
	import { useSectionStatus } from './section-status.svelte';
	import CoauthorActivityDialog from '$lib/components/coauthors/CoauthorActivityDialog.svelte';
	import * as Dialog from '$lib/components/ui/dialog';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Button } from '$lib/components/ui/button';
	import { Segmented } from '$lib/components/ui/segmented';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import { formatAgo } from '$lib/components/inference/endpoint-display';
	import Users from '@lucide/svelte/icons/users';
	import Link2 from '@lucide/svelte/icons/link-2';
	import Plus from '@lucide/svelte/icons/plus';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import ShieldAlert from '@lucide/svelte/icons/shield-alert';

	const EXPIRY = [
		{ value: '1', label: '1 day' },
		{ value: '7', label: '7 days' },
		{ value: '30', label: '30 days' },
	];
	const USES = [
		{ value: '1', label: '1 person' },
		{ value: '5', label: '5 people' },
		{ value: '20', label: '20 people' },
	];
	const INVITE_BADGE: Record<InviteStatus, { label: string; tone: Tone }> = {
		active: { label: 'Active', tone: 'success' },
		expired: { label: 'Expired', tone: 'warning' },
		used_up: { label: 'Used', tone: 'neutral' },
		revoked: { label: 'Revoked', tone: 'neutral' },
	};

	const workspace = getWorkspace();
	const origin = typeof window === 'undefined' ? '' : window.location.origin;

	let members = $state<CoauthorMember[]>([]);
	let invites = $state<CoauthorInvite[]>([]);
	let loading = $state(true);
	let error = $state('');
	let isStaff = $state(false);
	let showPastMembers = $state(false);
	let showPastLinks = $state(false);

	// New invite link dialog
	let createOpen = $state(false);
	let creating = $state(false);
	let createError = $state('');
	let label = $state('');
	let expiry = $state('7');
	let uses = $state('1');
	let created = $state<CoauthorInviteCreated | null>(null);

	// Confirmations
	let removing = $state<CoauthorMember | null>(null);
	let removeOpen = $state(false);
	let revoking = $state<CoauthorInvite | null>(null);
	let revokeOpen = $state(false);
	let leaving = $state<CoauthorWorkspace | null>(null);
	let leaveOpen = $state(false);
	let busy = $state(false);
	let opening = $state<number | null>(null);

	let activityOpen = $state(false);
	let activityFor = $state<CoauthorMember | null>(null);

	const activeMembers = $derived(members.filter((m) => m.status === 'active'));
	const pastMembers = $derived(members.filter((m) => m.status !== 'active'));
	const activeLinks = $derived(invites.filter((i) => i.status === 'active'));
	const pastLinks = $derived(invites.filter((i) => i.status !== 'active'));
	const visibleLinks = $derived(showPastLinks ? invites : activeLinks);

	const report = useSectionStatus('coauthors');
	$effect(() => {
		if (loading || error) return;
		const n = activeMembers.length;
		// Same shape as the token section's status ('2 active'); the badge capitalises words.
		report(n ? { tone: 'info', label: `${n} active` } : null);
	});

	async function load() {
		error = '';
		try {
			[members, invites] = await Promise.all([listMembers(), listInvites()]);
		} catch (e) {
			error = formatApiError(e, 'Could not load your co-authors.');
		} finally {
			loading = false;
		}
	}

	onMount(() => {
		void load();
		getMe()
			.then((me) => (isStaff = me.is_staff === true))
			.catch(() => {});
	});

	const date = (iso: string) => new Date(iso).toLocaleDateString();

	function openCreate() {
		label = '';
		expiry = '7';
		uses = '1';
		createError = '';
		created = null;
		createOpen = true;
	}

	async function create(e: Event) {
		e.preventDefault();
		creating = true;
		createError = '';
		try {
			const res = await createInvite({
				label: label.trim(),
				expires_in_days: Number(expiry),
				max_uses: Number(uses),
			});
			created = res;
			// The list keeps the summary only; the token lives in `created` until the dialog closes.
			const summary: CoauthorInvite = {
				id: res.id,
				label: res.label,
				token_prefix: res.token_prefix,
				status: res.status,
				max_uses: res.max_uses,
				uses: res.uses,
				expires_at: res.expires_at,
				created_at: res.created_at,
				revoked_at: res.revoked_at,
			};
			invites = [summary, ...invites];
		} catch (err) {
			createError = formatApiError(err, 'Could not create the link.');
		} finally {
			creating = false;
		}
	}

	// The link is shown once: forget it as soon as the dialog closes, however it closes.
	$effect(() => {
		if (!createOpen) created = null;
	});

	function askRevoke(invite: CoauthorInvite) {
		revoking = invite;
		revokeOpen = true;
	}

	async function revoke() {
		if (!revoking) return;
		const id = revoking.id;
		busy = true;
		try {
			await revokeInvite(id);
			invites = invites.map((i) =>
				i.id === id ? { ...i, status: 'revoked', revoked_at: new Date().toISOString() } : i,
			);
			revokeOpen = false;
			toast.success('Link revoked.');
		} catch (err) {
			toast.error(formatApiError(err, 'Could not revoke the link.'));
		} finally {
			busy = false;
		}
	}

	function askRemove(member: CoauthorMember) {
		removing = member;
		removeOpen = true;
	}

	async function remove() {
		if (!removing) return;
		const target = removing;
		busy = true;
		try {
			await removeMember(target.coauthorship_id);
			removeOpen = false;
			toast.success(`${target.member.display_name} no longer has access.`);
			// The link they joined with may have been revoked too.
			await load();
		} catch (err) {
			toast.error(formatApiError(err, 'Could not remove this co-author.'));
		} finally {
			busy = false;
		}
	}

	function showActivity(member: CoauthorMember) {
		activityFor = member;
		activityOpen = true;
	}

	async function open(ws: CoauthorWorkspace) {
		opening = ws.coauthorship_id;
		try {
			await workspace.switchTo(ws.coauthorship_id);
		} catch (err) {
			toast.error(formatApiError(err, 'Could not open this workspace.'));
			opening = null;
		}
	}

	function askLeave(ws: CoauthorWorkspace) {
		leaving = ws;
		leaveOpen = true;
	}

	async function leave() {
		if (!leaving) return;
		const target = leaving;
		busy = true;
		try {
			await leaveWorkspace(target.coauthorship_id);
			leaveOpen = false;
			toast.success(`You left ${target.owner.display_name}'s workspace.`);
			await workspace.load();
		} catch (err) {
			toast.error(formatApiError(err, 'Could not leave this workspace.'));
		} finally {
			busy = false;
		}
	}
</script>

{#snippet memberRow(m: CoauthorMember)}
	<div class="settings-row flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
		<div class="min-w-0 space-y-1">
			<p class="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-sm">
				<span class="truncate font-medium">{m.member.display_name}</span>
				{#if m.status === 'removed'}
					<StatusBadge status="removed" tone="neutral" label="Removed" />
				{:else if m.status === 'left'}
					<StatusBadge status="left" tone="neutral" label="Left" />
				{/if}
			</p>
			{#if m.member.display_name !== m.member.email}
				<p class="truncate text-xs text-muted-foreground">{m.member.email}</p>
			{/if}
			<p class="flex flex-wrap gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
				<span>Joined {formatAgo(m.joined_at)}</span>
				<span aria-hidden="true">·</span>
				{#if m.status !== 'active' && m.ended_at}
					<span>{m.status === 'left' ? 'Left' : 'Removed'} {formatAgo(m.ended_at)}</span>
				{:else if m.last_active_at}
					<span>Last active {formatAgo(m.last_active_at)}</span>
				{:else}
					<span>Not active yet</span>
				{/if}
			</p>
		</div>
		<div class="flex shrink-0 items-center gap-1.5">
			<Button variant="outline" size="sm" onclick={() => showActivity(m)}>Activity</Button>
			{#if m.status === 'active'}
				<Button
					variant="ghost"
					size="sm"
					class="text-muted-foreground hover:bg-destructive/10 hover:text-danger-fg"
					onclick={() => askRemove(m)}
					aria-label="Remove {m.member.display_name}"
				>
					Remove
				</Button>
			{/if}
		</div>
	</div>
{/snippet}

{#if error}
	<div class="settings-row"><ErrorAlert message={error} onretry={load} /></div>
{:else if loading}
	<LoadingState label="loading co-authors" />
{:else}
	<SettingsBlock title="People with access">
		{#snippet description()}
			They can see and change everything in your workspace.
		{/snippet}
		{#snippet actions()}
			{#if pastMembers.length}
				<Button variant="ghost" size="sm" onclick={() => (showPastMembers = !showPastMembers)} aria-pressed={showPastMembers}>
					{showPastMembers ? 'Hide past co-authors' : 'Show past co-authors'}
				</Button>
			{/if}
		{/snippet}

		{#if activeMembers.length === 0}
			<EmptyState
				compact
				class="px-4"
				icon={Users}
				title="No co-authors yet"
				description="Create an invite link and send it to the person you want to work with."
			/>
		{:else}
			{#each activeMembers as m (m.coauthorship_id)}{@render memberRow(m)}{/each}
		{/if}
		{#if showPastMembers}
			{#each pastMembers as m (m.coauthorship_id)}{@render memberRow(m)}{/each}
		{/if}
	</SettingsBlock>

	<SettingsBlock title="Invite links">
		{#snippet description()}
			<span class="tabular-nums">{activeLinks.length}</span> active{#if pastLinks.length}{' '}·
				<span class="tabular-nums">{pastLinks.length}</span> past{/if}
		{/snippet}
		{#snippet actions()}
			{#if pastLinks.length}
				<Button variant="ghost" size="sm" onclick={() => (showPastLinks = !showPastLinks)} aria-pressed={showPastLinks}>
					{showPastLinks ? 'Hide past links' : 'Show past links'}
				</Button>
			{/if}
			<Button size="sm" onclick={openCreate}><Plus />New invite link</Button>
		{/snippet}

		{#if visibleLinks.length === 0}
			<EmptyState
				compact
				class="px-4"
				icon={Link2}
				title="No active invite links"
				description="A link lets one or more people join your workspace. You see it once, when you create it."
			/>
		{:else}
			{#each visibleLinks as invite (invite.id)}
				{@const badge = INVITE_BADGE[invite.status]}
				<div
					class="settings-row flex items-center justify-between gap-4 {invite.status === 'active' ? '' : 'opacity-70'}"
				>
					<div class="min-w-0 space-y-1">
						<p class="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-sm">
							<span class="truncate font-medium">{invite.label || 'Invite link'}</span>
							<StatusBadge status={invite.status} tone={badge.tone} label={badge.label} />
						</p>
						<p class="flex flex-wrap gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
							<code class="font-mono">{invite.token_prefix}…</code>
							<span aria-hidden="true">·</span>
							<span class="tabular-nums">{invite.uses} of {invite.max_uses} used</span>
							<span aria-hidden="true">·</span>
							{#if invite.status === 'revoked' && invite.revoked_at}
								<span>revoked {date(invite.revoked_at)}</span>
							{:else}
								<span>{invite.status === 'expired' ? 'expired' : 'expires'} {date(invite.expires_at)}</span>
							{/if}
						</p>
					</div>
					{#if invite.status === 'active'}
						<Button
							variant="ghost"
							size="sm"
							class="shrink-0 text-muted-foreground hover:bg-destructive/10 hover:text-danger-fg"
							onclick={() => askRevoke(invite)}
							aria-label="Revoke {invite.label || 'invite link'} ({invite.token_prefix}…)"
						>
							Revoke
						</Button>
					{/if}
				</div>
			{/each}
		{/if}
	</SettingsBlock>
{/if}

{#if workspace.workspaces.length > 0}
	<SettingsBlock title="Workspaces you can work in" description="People who invited you as a co-author.">
		{#each workspace.workspaces as ws (ws.coauthorship_id)}
			<div class="settings-row flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
				<div class="min-w-0 space-y-1">
					<p class="truncate text-sm font-medium">{ws.owner.display_name}</p>
					{#if ws.owner.display_name !== ws.owner.email}
						<p class="truncate text-xs text-muted-foreground">{ws.owner.email}</p>
					{/if}
					<p class="text-xs text-muted-foreground">Joined {formatAgo(ws.joined_at)}</p>
				</div>
				<div class="flex shrink-0 items-center gap-1.5">
					<Button
						variant="outline"
						size="sm"
						onclick={() => void open(ws)}
						disabled={opening !== null}
						aria-busy={opening === ws.coauthorship_id}
						aria-label="Open {ws.owner.display_name}'s workspace"
					>
						{#if opening === ws.coauthorship_id}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{/if}
						Open
					</Button>
					<Button
						variant="ghost"
						size="sm"
						class="text-muted-foreground hover:bg-destructive/10 hover:text-danger-fg"
						onclick={() => askLeave(ws)}
						aria-label="Leave {ws.owner.display_name}'s workspace"
					>
						Leave
					</Button>
				</div>
			</div>
		{/each}
	</SettingsBlock>
{/if}

<Dialog.Root bind:open={createOpen}>
	<Dialog.Content class="w-[calc(100%-2rem)] max-w-md">
		{#if created}
			<div class="space-y-5">
				<Dialog.Header>
					<Dialog.Title>Invite link created</Dialog.Title>
					<Dialog.Description>Copy this link now. It is not shown again.</Dialog.Description>
				</Dialog.Header>
				<CopyField value={origin + created.path} label="invite link" />
				<p class="text-[13px] leading-5 text-muted-foreground">
					{created.max_uses === 1 ? 'One person' : `Up to ${created.max_uses} people`} can join with it until
					{new Date(created.expires_at).toLocaleDateString()}. Send it only to the person you are inviting.
				</p>
				<Dialog.Footer>
					<Button size="sm" onclick={() => (createOpen = false)}>Done</Button>
				</Dialog.Footer>
			</div>
		{:else}
			<form onsubmit={create} class="space-y-5">
				<Dialog.Header>
					<Dialog.Title>New invite link</Dialog.Title>
					<Dialog.Description>Whoever joins with it can work in your workspace as a co-author.</Dialog.Description>
				</Dialog.Header>
				<div class="space-y-1.5">
					<Label for="invite-label">Label <span class="font-normal text-muted-foreground">(optional)</span></Label>
					<Input
						id="invite-label"
						placeholder="Who is this for?"
						bind:value={label}
						maxlength={120}
						autocomplete="off"
					/>
				</div>
				<div class="space-y-1.5">
					<span class="text-sm font-medium" id="invite-expiry-label">Expires</span>
					<Segmented aria-labelledby="invite-expiry-label" bind:value={expiry} items={EXPIRY} fullWidth size="sm" />
				</div>
				<div class="space-y-1.5">
					<span class="text-sm font-medium" id="invite-uses-label">Can be used by</span>
					<Segmented aria-labelledby="invite-uses-label" bind:value={uses} items={USES} fullWidth size="sm" />
				</div>
				<p class="flex items-start gap-2 text-[13px] leading-5 text-muted-foreground">
					<TriangleAlert class="mt-0.5 size-4 shrink-0" aria-hidden="true" />
					<span>
						Anyone who has this link can join until it is used or expires. Only send it to someone you trust: a
						co-author can change and delete everything in your workspace.
					</span>
				</p>
				{#if isStaff}
					<div class="flex items-start gap-2 rounded-md border border-warning/40 bg-warning/10 p-3 text-sm text-warning-fg" role="note">
						<ShieldAlert class="mt-0.5 size-4 shrink-0" aria-hidden="true" />
						<p>
							You are an administrator. A co-author gets your administrator access, including other people's work
							and system settings.
						</p>
					</div>
				{/if}
				{#if createError}<ErrorAlert message={createError} />{/if}
				<Dialog.Footer>
					<Button type="button" variant="outline" size="sm" onclick={() => (createOpen = false)} disabled={creating}>
						Cancel
					</Button>
					<Button type="submit" size="sm" disabled={creating} aria-busy={creating}>
						{#if creating}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{/if}
						Create link
					</Button>
				</Dialog.Footer>
			</form>
		{/if}
	</Dialog.Content>
</Dialog.Root>

<CoauthorActivityDialog bind:open={activityOpen} member={activityFor} />

<ConfirmDialog
	bind:open={removeOpen}
	title="Remove {removing?.member.display_name ?? 'co-author'}?"
	description="They lose access right away, and the invite link they joined with stops working. Runs they started keep going. Keys, tokens, webhooks, schedules and evaluator accounts they created in your workspace stay until you delete them; check their activity."
	confirmLabel="Remove"
	destructive
	busy={busy}
	onConfirm={remove}
/>

<ConfirmDialog
	bind:open={revokeOpen}
	title="Revoke this link?"
	description="Nobody can join with it any more. People who already joined keep their access."
	confirmLabel="Revoke"
	destructive
	busy={busy}
	onConfirm={revoke}
/>

<ConfirmDialog
	bind:open={leaveOpen}
	title="Leave {leaving?.owner.display_name ?? 'this'}'s workspace?"
	description="You lose access right away. They can invite you again."
	confirmLabel="Leave"
	destructive
	busy={busy}
	onConfirm={leave}
/>
