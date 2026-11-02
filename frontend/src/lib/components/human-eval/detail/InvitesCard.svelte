<script lang="ts">
	/**
	 * InvitesCard — shareable join links. Each visitor of a link becomes a new
	 * participant (until max uses / expiry). Shows the full URL (this site's
	 * origin + the invite path) with copy, uses, expiry, an active toggle,
	 * label edits and delete; a small form creates new links.
	 *
	 * Props: `task`, `onchange()` (participant counts may change).
	 */
	import { untrack } from 'svelte';
	import { toast } from 'svelte-sonner';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import * as Card from '$lib/components/ui/card';
	import * as Dialog from '$lib/components/ui/dialog';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Switch } from '$lib/components/ui/switch';
	import { formatApiError } from '$lib/api/core';
	import { createInvite, deleteInvite, humanEvalErrorBody, listInvites, updateInvite, type Invite, type TaskDetail } from '$lib/api/human-eval';
	import { formatDateTime } from '$lib/utils/formatters';
	import { cn } from '$lib/utils';
	import { absoluteUrl, plural } from '../format';
	import { copyText } from './browser';
	import Copy from '@lucide/svelte/icons/copy';
	import Link from '@lucide/svelte/icons/link';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Plus from '@lucide/svelte/icons/plus';
	import Trash2 from '@lucide/svelte/icons/trash-2';

	interface Props {
		task: TaskDetail;
		onchange?: () => void;
	}

	let { task, onchange }: Props = $props();

	const enabled = $derived(task.settings.access.allow_invite_links);
	const origin = $derived(typeof window === 'undefined' ? '' : window.location.origin);

	let invites = $state.raw<Invite[]>([]);
	let loading = $state(true);
	let loadError = $state('');

	async function load() {
		loading = true;
		try {
			invites = await listInvites(task.id);
			loadError = '';
		} catch (e) {
			loadError = formatApiError(e, 'Could not load invite links.');
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		void task.id;
		untrack(() => void load());
	});

	function errorText(e: unknown, fallback: string): string {
		const body = humanEvalErrorBody(e);
		if (body?.code === 'access_disabled') return 'Invite links are turned off in this task’s settings (Design → Access).';
		return body?.detail ?? formatApiError(e, fallback);
	}

	function expired(invite: Invite): boolean {
		return !!invite.expires_at && new Date(invite.expires_at).getTime() < Date.now();
	}
	function full(invite: Invite): boolean {
		return invite.max_uses != null && invite.uses >= invite.max_uses;
	}

	// ── create / edit ───────────────────────────────────────────────
	let formOpen = $state(false);
	let editing = $state<Invite | null>(null);
	let label = $state('');
	let maxUsesText = $state('');
	let expiresText = $state('');
	let saving = $state(false);
	let formError = $state('');

	function toLocalInput(iso: string | null): string {
		if (!iso) return '';
		const d = new Date(iso);
		const pad = (n: number) => String(n).padStart(2, '0');
		return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
	}

	function openForm(invite: Invite | null) {
		editing = invite;
		label = invite?.label ?? '';
		maxUsesText = invite?.max_uses != null ? String(invite.max_uses) : '';
		expiresText = toLocalInput(invite?.expires_at ?? null);
		formError = '';
		formOpen = true;
	}

	const maxUses = $derived.by(() => {
		const raw = String(maxUsesText ?? '').trim();
		if (!raw) return null;
		const n = Number(raw);
		return Number.isInteger(n) && n >= 1 ? n : NaN;
	});

	async function save(e: SubmitEvent) {
		e.preventDefault();
		if (Number.isNaN(maxUses)) {
			formError = 'Max uses must be a whole number of at least 1, or empty for unlimited.';
			return;
		}
		saving = true;
		formError = '';
		const input = {
			label: label.trim(),
			max_uses: maxUses,
			expires_at: expiresText ? new Date(expiresText).toISOString() : null,
		};
		try {
			if (editing) {
				const updated = await updateInvite(task.id, editing.id, input);
				invites = invites.map((i) => (i.id === updated.id ? updated : i));
				toast.success('Invite link updated');
			} else {
				const created = await createInvite(task.id, input);
				invites = [created, ...invites];
				toast.success('Invite link created', {
					action: { label: 'Copy', onClick: () => void copyText(absoluteUrl(origin, created.path), 'Link copied') },
				});
			}
			formOpen = false;
		} catch (err) {
			formError = errorText(err, 'Could not save the invite link.');
		} finally {
			saving = false;
		}
	}

	let busy = $state<string | null>(null);
	async function toggle(invite: Invite, next: boolean) {
		busy = invite.id;
		invites = invites.map((i) => (i.id === invite.id ? { ...i, is_active: next } : i));
		try {
			const updated = await updateInvite(task.id, invite.id, { is_active: next });
			invites = invites.map((i) => (i.id === updated.id ? updated : i));
			toast.success(next ? 'Invite link active' : 'Invite link disabled: new visitors can no longer join');
		} catch (err) {
			invites = invites.map((i) => (i.id === invite.id ? { ...i, is_active: !next } : i));
			toast.error(errorText(err, 'Could not update the invite link.'));
		} finally {
			busy = null;
		}
	}

	let deleteTarget = $state<Invite | null>(null);
	let deleteOpen = $state(false);
	let deleting = $state(false);
	async function confirmDelete() {
		const target = deleteTarget;
		if (!target) return;
		deleting = true;
		try {
			await deleteInvite(task.id, target.id);
			invites = invites.filter((i) => i.id !== target.id);
			deleteOpen = false;
			toast.success('Invite link deleted');
			onchange?.();
		} catch (err) {
			deleteOpen = false;
			toast.error(errorText(err, 'Could not delete the invite link.'));
		} finally {
			deleting = false;
		}
	}
</script>

<Card.Root class="gap-0 overflow-clip py-0">
	<div class="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-2.5">
		<div class="min-w-0">
			<Card.Title>Invite links</Card.Title>
			<p class="text-xs text-muted-foreground">Anyone with a link can join as a new participant (anonymous, gets a resume code).</p>
		</div>
		<Button size="sm" variant="outline" disabled={!enabled} title={enabled ? undefined : 'Invite links are off (Design → Access)'} onclick={() => openForm(null)}>
			<Plus />New link
		</Button>
	</div>
	{#if !enabled}
		<p class="px-4 py-3 text-sm text-muted-foreground">Invite links are turned off for this task (Design → Access). Existing links don’t work while off.</p>
	{/if}
	{#if loadError}
		<div class="p-3"><ErrorAlert message={loadError} onretry={load} /></div>
	{:else if loading}
		<p class="px-4 py-3 text-sm text-muted-foreground">Loading invite links…</p>
	{:else if invites.length === 0}
		{#if enabled}<p class="px-4 py-3 text-sm text-muted-foreground">No invite links yet.</p>{/if}
	{:else}
		<ul class="divide-y divide-border/60">
			{#each invites as invite (invite.id)}
				{@const url = absoluteUrl(origin, invite.path)}
				{@const dead = !invite.is_active || expired(invite) || full(invite)}
				<li class={cn('space-y-2 px-4 py-3', dead && 'bg-muted/30')}>
					<div class="flex flex-wrap items-center justify-between gap-2">
						<div class="flex min-w-0 items-center gap-2">
							<Link class="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
							<button type="button" class="min-w-0 cursor-pointer truncate text-left text-sm font-medium hover:text-primary" onclick={() => openForm(invite)} title="Edit">
								{invite.label || 'Untitled link'}
							</button>
							{#if !invite.is_active}<span class="text-[11px] text-muted-foreground">disabled</span>
							{:else if expired(invite)}<span class="text-[11px] text-warning-fg">expired</span>
							{:else if full(invite)}<span class="text-[11px] text-warning-fg">full</span>{/if}
						</div>
						<div class="flex items-center gap-1">
							<span class="mr-2 font-mono text-xs tabular-nums text-muted-foreground" title="Participants who joined through this link">
								{invite.uses}{invite.max_uses != null ? `/${invite.max_uses}` : ''} {invite.uses === 1 ? 'use' : 'uses'}
							</span>
							<Switch size="sm" checked={invite.is_active} disabled={busy === invite.id || !enabled} label="{invite.is_active ? 'Disable' : 'Enable'} {invite.label || 'link'}" onCheckedChange={(v) => toggle(invite, v)} />
							<Button
								variant="ghost"
								size="sm"
								class="size-8 p-0 text-danger-fg"
								aria-label="Delete {invite.label || 'link'}"
								title="Delete the link (participants who joined keep their data)"
								onclick={() => {
									deleteTarget = invite;
									deleteOpen = true;
								}}
							>
								<Trash2 />
							</Button>
						</div>
					</div>
					<div class="flex h-8 min-w-0 items-center rounded-md border border-input bg-muted/40">
						<code class="min-w-0 flex-1 truncate px-3 font-mono text-xs" title={url}>{url}</code>
						<button
							type="button"
							class="flex h-full shrink-0 cursor-pointer items-center gap-1.5 border-l border-input px-3 text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
							onclick={() => copyText(url, 'Link copied')}
							aria-label="Copy the link {invite.label}"
						>
							<Copy class="size-3.5" aria-hidden="true" />Copy
						</button>
					</div>
					<p class="text-[11px] text-muted-foreground">
						Created {formatDateTime(invite.created_at)}
						{#if invite.expires_at} · {expired(invite) ? 'expired' : 'expires'} {formatDateTime(invite.expires_at)}{/if}
						{#if invite.max_uses != null} · {plural(Math.max(0, invite.max_uses - invite.uses), 'join')} left{/if}
					</p>
				</li>
			{/each}
		</ul>
	{/if}
</Card.Root>

<Dialog.Root bind:open={formOpen}>
	<Dialog.Content class="max-w-md">
		<Dialog.Header>
			<Dialog.Title>{editing ? 'Edit invite link' : 'New invite link'}</Dialog.Title>
			<Dialog.Description>The label is for you (e.g. the course or channel you share it in); participants don’t see it.</Dialog.Description>
		</Dialog.Header>
		<form class="space-y-4" onsubmit={save}>
			<div class="space-y-1.5">
				<Label for="he-invite-label">Label</Label>
				<Input id="he-invite-label" bind:value={label} maxlength={120} placeholder="e.g. Course SE-2 group A" />
			</div>
			<div class="grid gap-4 sm:grid-cols-2">
				<div class="space-y-1.5">
					<Label for="he-invite-max">Max uses</Label>
					<Input id="he-invite-max" type="number" inputmode="numeric" min="1" step="1" bind:value={maxUsesText} placeholder="unlimited" />
				</div>
				<div class="space-y-1.5">
					<Label for="he-invite-expires">Expires</Label>
					<Input id="he-invite-expires" type="datetime-local" bind:value={expiresText} />
				</div>
			</div>
			{#if formError}<p class="text-sm text-danger-fg" role="alert">{formError}</p>{/if}
			<Dialog.Footer>
				<Button variant="outline" size="sm" type="button" disabled={saving} onclick={() => (formOpen = false)}>Cancel</Button>
				<Button size="sm" type="submit" disabled={saving} aria-busy={saving}>
					{#if saving}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{/if}
					{editing ? 'Save' : 'Create link'}
				</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>

<ConfirmDialog
	bind:open={deleteOpen}
	title="Delete this invite link?"
	description="The link stops working immediately. Participants who already joined through it keep their data."
	confirmLabel="Delete link"
	destructive
	busy={deleting}
	onConfirm={confirmDelete}
/>
