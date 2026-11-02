<script lang="ts">
	/**
	 * ParticipantsTab — how people get in and who they are.
	 *
	 * - Access: invite links (InvitesCard), access codes (CodesDialog: shown
	 *   once, copy all / CSV / printable slips) and accounts (AccountsDialog:
	 *   per-email results with set-password links).
	 * - Participants table (filters: search, kind, status; paginated): status,
	 *   progress, quality flags, exclusion; row actions: exclude / include
	 *   (with reason), note, new access code, new set-password link, withdraw,
	 *   delete (all data).
	 *
	 * Props: `task`, `onreload()` (the page's counts changed).
	 */
	import { untrack } from 'svelte';
	import { toast } from 'svelte-sonner';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import PaginationBar from '$lib/components/PaginationBar.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import * as Card from '$lib/components/ui/card';
	import * as Dialog from '$lib/components/ui/dialog';
	import * as Select from '$lib/components/ui/select';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Textarea } from '$lib/components/ui/textarea';
	import { TableToolbar, tableFrame } from '$lib/components/table';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import {
		deleteParticipant,
		humanEvalErrorBody,
		issueSetupLink,
		listParticipants,
		regenerateAccessCode,
		updateParticipant,
		type ParticipantKind,
		type ParticipantStatus,
		type ResearcherParticipant,
		type TaskDetail,
	} from '$lib/api/human-eval';
	import { formatDateTime } from '$lib/utils/formatters';
	import { cn } from '$lib/utils';
	import { PARTICIPANT_KIND_LABELS, absoluteUrl, plural, type ConfirmCopy } from '../format';
	import AccountsDialog from './AccountsDialog.svelte';
	import CodesDialog from './CodesDialog.svelte';
	import ExclusionDialog from './ExclusionDialog.svelte';
	import FlagList from './FlagList.svelte';
	import InvitesCard from './InvitesCard.svelte';
	import SecretDialog from './SecretDialog.svelte';
	import Ban from '@lucide/svelte/icons/ban';
	import KeyRound from '@lucide/svelte/icons/key-round';
	import Link from '@lucide/svelte/icons/link';
	import Mail from '@lucide/svelte/icons/mail';
	import SquarePen from '@lucide/svelte/icons/square-pen';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import UserCheck from '@lucide/svelte/icons/user-check';
	import UserX from '@lucide/svelte/icons/user-x';
	import Users from '@lucide/svelte/icons/users';

	interface Props {
		task: TaskDetail;
		onreload?: () => void;
	}

	let { task, onreload }: Props = $props();

	const access = $derived(task.settings.access);
	const postHoc = $derived(!!task.opened_at);

	// ── list ────────────────────────────────────────────────────────
	let search = $state('');
	let kind = $state('');
	let status = $state('');
	let pageNo = $state(1);
	const PER_PAGE = 50;

	let rows = $state.raw<ResearcherParticipant[]>([]);
	let total = $state(0);
	let pages = $state(1);
	let loading = $state(true);
	let loadError = $state('');
	let ctl: AbortController | null = null;

	async function load(quiet = false) {
		ctl?.abort();
		const mine = new AbortController();
		ctl = mine;
		if (!quiet) loading = true;
		try {
			const res = await listParticipants(
				task.id,
				{
					search: search.trim() || undefined,
					kind: (kind || undefined) as ParticipantKind | undefined,
					status: (status || undefined) as ParticipantStatus | undefined,
					page: pageNo,
					per_page: PER_PAGE,
				},
				{ signal: mine.signal }
			);
			rows = res.items;
			total = res.total;
			pages = res.pages;
			loadError = '';
		} catch (e) {
			if (isAbortError(e) && mine.signal.aborted) return;
			if (mine.signal.aborted) return;
			loadError = formatApiError(e, 'Could not load participants.');
		} finally {
			if (ctl === mine) loading = false;
		}
	}

	const queryKey = $derived(JSON.stringify([task.id, search.trim(), kind, status, pageNo]));
	let searchTimer: ReturnType<typeof setTimeout> | undefined;
	$effect(() => {
		void queryKey;
		untrack(() => {
			clearTimeout(searchTimer);
			searchTimer = setTimeout(() => void load(rows.length > 0), 200);
		});
		return () => clearTimeout(searchTimer);
	});

	function resetPage() {
		pageNo = 1;
	}

	function refreshAll() {
		void load(true);
		onreload?.();
	}

	function replaceRow(next: ResearcherParticipant) {
		rows = rows.map((r) => (r.id === next.id ? next : r));
	}

	function errorText(e: unknown, fallback: string): string {
		const body = humanEvalErrorBody(e);
		return body?.detail || formatApiError(e, fallback);
	}

	// ── dialogs ─────────────────────────────────────────────────────
	let codesOpen = $state(false);
	let accountsOpen = $state(false);

	let exclusionOpen = $state(false);
	let exclusionTarget = $state<ResearcherParticipant | null>(null);
	function askExclusion(p: ResearcherParticipant) {
		exclusionTarget = p;
		exclusionOpen = true;
	}
	async function submitExclusion(excluded: boolean, reason: string) {
		const p = exclusionTarget;
		if (!p) return;
		try {
			const next = await updateParticipant(task.id, p.id, { excluded, exclusion_reason: reason });
			replaceRow(next);
			toast.success(excluded ? `${p.label} excluded from analysis` : `${p.label} included again`);
		} catch (e) {
			throw new Error(errorText(e, 'Could not update the participant.'));
		}
	}

	let noteOpen = $state(false);
	let noteTarget = $state<ResearcherParticipant | null>(null);
	let noteText = $state('');
	let noteSaving = $state(false);
	function askNote(p: ResearcherParticipant) {
		noteTarget = p;
		noteText = p.note;
		noteOpen = true;
	}
	async function saveNote(e: SubmitEvent) {
		e.preventDefault();
		const p = noteTarget;
		if (!p) return;
		noteSaving = true;
		try {
			replaceRow(await updateParticipant(task.id, p.id, { note: noteText }));
			noteOpen = false;
			toast.success('Note saved');
		} catch (err) {
			toast.error(errorText(err, 'Could not save the note.'));
		} finally {
			noteSaving = false;
		}
	}

	let secretOpen = $state(false);
	let secret = $state({ title: '', description: '', value: '', label: '', note: '' });

	async function newSetupLink(p: ResearcherParticipant) {
		busy = p.id;
		try {
			const link = await issueSetupLink(task.id, p.id);
			secret = {
				title: `Set-password link for ${p.label}`,
				description: p.email ? `Send it to ${p.email} only.` : 'Send it only to the account owner.',
				value: absoluteUrl(window.location.origin, link.setup_path),
				label: 'set-password link',
				note: `Valid until ${formatDateTime(link.expires_at)} or until the account signs in.`,
			};
			secretOpen = true;
		} catch (e) {
			const body = humanEvalErrorBody(e);
			toast.error(
				body?.code === 'forbidden'
					? 'Only for accounts you created that have never signed in. Existing users reset their password themselves.'
					: errorText(e, 'Could not create a link.')
			);
		} finally {
			busy = null;
		}
	}

	// ── confirmed actions ───────────────────────────────────────────
	type Pending = { kind: 'withdraw' | 'delete' | 'code'; p: ResearcherParticipant } | null;
	let confirmOpen = $state(false);
	let confirming = $state<Pending>(null);
	let confirmCopy = $state<ConfirmCopy>({ title: '', description: '', label: '' });
	let confirmBusy = $state(false);
	let busy = $state<string | null>(null);

	function ask(kindOf: 'withdraw' | 'delete' | 'code', p: ResearcherParticipant) {
		confirming = { kind: kindOf, p };
		confirmCopy =
			kindOf === 'withdraw'
				? {
						title: `Withdraw ${p.label}?`,
						description:
							'Their open assignments expire and they can no longer work or sign in to this task. Submitted work is kept; exclude them as well to leave it out of the results.',
						label: 'Withdraw',
						destructive: true,
					}
				: kindOf === 'delete'
					? {
							title: `Delete ${p.label} and all their data?`,
							description:
								'Their assignments, answers, observations, screenshots, finding verdicts and HTTP logs are deleted, and their session ends. Use this for data-deletion requests. After the task opened, deleting someone with submitted work is recorded in the protocol log and makes the results exploratory. This cannot be undone.',
							label: 'Delete participant',
							destructive: true,
						}
					: {
							title: `Replace ${p.label}’s access code?`,
							description: 'The old code stops working and their current session is signed out. You will see the new code once.',
							label: 'New code',
						};
		confirmOpen = true;
	}

	async function onConfirm() {
		const c = confirming;
		if (!c) return;
		confirmBusy = true;
		try {
			if (c.kind === 'withdraw') {
				replaceRow(await updateParticipant(task.id, c.p.id, { status: 'withdrawn' }));
				toast.success(`${c.p.label} withdrawn`);
				onreload?.();
			} else if (c.kind === 'delete') {
				await deleteParticipant(task.id, c.p.id);
				toast.success(`${c.p.label} and their data deleted`);
				refreshAll();
			} else {
				const code = await regenerateAccessCode(task.id, c.p.id);
				secret = {
					title: `New access code for ${code.label}`,
					description: 'Shown only now. Hand it to the participant; they enter it on the portal to continue.',
					value: code.code,
					label: 'access code',
					note: `Portal: ${absoluteUrl(window.location.origin, '/eval')}`,
				};
				secretOpen = true;
				void load(true);
			}
			confirmOpen = false;
		} catch (e) {
			confirmOpen = false;
			toast.error(errorText(e, 'The action failed.'));
		} finally {
			confirmBusy = false;
		}
	}

	const KIND_ITEMS = [
		{ value: 'invite', label: 'Invite link' },
		{ value: 'code', label: 'Access code' },
		{ value: 'account', label: 'Account' },
		{ value: 'preview', label: 'Preview' },
	];
	const STATUS_ITEMS = [
		{ value: 'invited', label: 'Invited' },
		{ value: 'active', label: 'Active' },
		{ value: 'completed', label: 'Completed' },
		{ value: 'withdrawn', label: 'Withdrawn' },
	];
	const filtered = $derived(!!(search.trim() || kind || status));

	function identity(p: ResearcherParticipant): string {
		if (p.kind === 'account') return p.email ?? '';
		if (p.kind === 'invite') return p.invite?.label ? `via ${p.invite.label}` : 'via invite link';
		if (p.kind === 'code' && p.access_code_hint) return `code …${p.access_code_hint}`;
		return '';
	}
</script>

<div class="space-y-4">
	<div class="grid gap-4 lg:grid-cols-[minmax(0,1fr)_20rem]">
		<InvitesCard {task} onchange={refreshAll} />
		<div class="space-y-4">
			<Card.Root class="gap-2 py-3">
				<div class="flex items-start justify-between gap-2 px-4">
					<div class="min-w-0">
						<Card.Title>Access codes</Card.Title>
						<p class="text-xs text-muted-foreground">Pre-made participants with a code each (classes, printed slips).</p>
					</div>
					<KeyRound class="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
				</div>
				<div class="px-4">
					{#if access.allow_access_codes}
						<Button size="sm" variant="outline" onclick={() => (codesOpen = true)}><KeyRound />Generate codes</Button>
					{:else}
						<p class="text-xs text-muted-foreground">Turned off (Design → Access).</p>
					{/if}
				</div>
			</Card.Root>
			<Card.Root class="gap-2 py-3">
				<div class="flex items-start justify-between gap-2 px-4">
					<div class="min-w-0">
						<Card.Title>Accounts</Card.Title>
						<p class="text-xs text-muted-foreground">Named participants who sign in; new ones get restricted evaluator accounts.</p>
					</div>
					<Mail class="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
				</div>
				<div class="px-4">
					{#if access.allow_accounts}
						<Button size="sm" variant="outline" onclick={() => (accountsOpen = true)}><Mail />Add accounts</Button>
					{:else}
						<p class="text-xs text-muted-foreground">Turned off (Design → Access).</p>
					{/if}
				</div>
			</Card.Root>
		</div>
	</div>

	<Card.Root class="gap-0 overflow-clip py-0">
		<div class="flex flex-wrap items-center gap-2 border-b border-border p-2" role="search">
			<Input class="h-8 w-full sm:w-56" placeholder="Search label, email…" aria-label="Search participants" bind:value={search} oninput={resetPage} />
			<Select.Simple
				size="sm"
				class="w-36"
				aria-label="Kind"
				placeholder="Any kind"
				clearLabel="Any kind"
				items={KIND_ITEMS}
				bind:value={kind}
				onValueChange={resetPage}
			/>
			<Select.Simple
				size="sm"
				class="w-36"
				aria-label="Status"
				placeholder="Any status"
				clearLabel="Any status"
				items={STATUS_ITEMS}
				bind:value={status}
				onValueChange={resetPage}
			/>
		</div>
		<TableToolbar>
			{#snippet summary()}
				{plural(total, 'participant')}{filtered ? ' match' : ''} · flags are computed from submitted work
			{/snippet}
		</TableToolbar>
		{#if loadError}
			<div class="p-3"><ErrorAlert message={loadError} onretry={() => load()} /></div>
		{:else if loading && rows.length === 0}
			<LoadingState label="loading participants" />
		{:else if rows.length === 0}
			<EmptyState
				compact
				icon={Users}
				title={filtered ? 'No participants match' : 'No participants yet'}
				description={filtered ? 'Change the filters.' : 'Share an invite link, generate access codes or add accounts.'}
			/>
		{:else}
			<div class="table-frame" {@attach tableFrame}>
				<table class="min-w-[60rem]">
					<thead>
						<tr>
							<th class="col-sticky">Participant</th>
							<th>Status</th>
							<th class="num" title="Submitted / skipped / in progress">Work</th>
							<th>Last seen</th>
							<th>Quality flags</th>
							<th>Exclusion · note</th>
							<th class="text-right">Actions</th>
						</tr>
					</thead>
					<tbody>
						{#each rows as p (p.id)}
							{@const working = busy === p.id}
							<tr class={cn(p.excluded && 'bg-muted/30')}>
								<td class="col-sticky min-w-48">
									{#if p.submitted + p.skipped + p.in_progress > 0}
										<a class="font-mono font-medium hover:text-primary" href="?tab=responses&participant={p.id}" title="Show {p.label}'s assignments">{p.label}</a>
									{:else}
										<span class="font-mono font-medium">{p.label}</span>
									{/if}
									<span class="ml-1 text-xs text-muted-foreground">{PARTICIPANT_KIND_LABELS[p.kind] ?? p.kind}</span>
									{#if identity(p)}
										<span class="cell-sub block max-w-60 truncate text-xs text-muted-foreground" title={identity(p)}>{identity(p)}</span>
									{/if}
								</td>
								<td>
									<StatusBadge status={p.status} />
									{#if p.excluded}<span class="ml-1 text-[11px] font-medium text-danger-fg">excluded</span>{/if}
									{#if p.completed_at}<span class="cell-sub block text-[11px] text-muted-foreground">done {formatDateTime(p.completed_at)}</span>{/if}
								</td>
								<td class="num text-xs">
									{p.submitted}<span class="text-muted-foreground"> / {p.skipped} / {p.in_progress}</span>
								</td>
								<td class="whitespace-nowrap text-xs text-muted-foreground">{p.last_seen_at ? formatDateTime(p.last_seen_at) : 'never'}</td>
								<td class="max-w-56"><FlagList flags={p.quality_flags} /></td>
								<td class="max-w-64 text-xs">
									{#if p.excluded}
										<span class="block break-words">{p.exclusion_reason || 'No reason given'}</span>
									{/if}
									{#if p.note}
										<span class="cell-sub block break-words text-muted-foreground" title="Researcher note">{p.note}</span>
									{/if}
									{#if !p.excluded && !p.note}<span class="text-muted-foreground">—</span>{/if}
								</td>
								<td>
									<div class="flex items-center justify-end gap-0.5">
										{#if p.kind !== 'preview'}
											<Button variant="ghost" size="sm" class="h-8 px-2 text-xs" disabled={working} onclick={() => askExclusion(p)}>
												{#if p.excluded}<UserCheck />Include{:else}<Ban />Exclude{/if}
											</Button>
										{/if}
										<span class="row-actions-more flex items-center gap-0.5">
											<Button variant="ghost" size="sm" class="size-8 p-0" disabled={working} title="Researcher note" aria-label="Note for {p.label}" onclick={() => askNote(p)}>
												<SquarePen />
											</Button>
											{#if (p.kind === 'code' || p.kind === 'invite') && p.status !== 'withdrawn'}
												<Button variant="ghost" size="sm" class="size-8 p-0" disabled={working} title="Replace the access code (lost code)" aria-label="New access code for {p.label}" onclick={() => ask('code', p)}>
													<KeyRound />
												</Button>
											{/if}
											{#if p.kind === 'account' && p.last_seen_at == null && p.status !== 'withdrawn'}
												<Button variant="ghost" size="sm" class="size-8 p-0" disabled={working} title="New set-password link (accounts you created that never signed in)" aria-label="New set-password link for {p.label}" onclick={() => newSetupLink(p)}>
													<Link />
												</Button>
											{/if}
											{#if p.status !== 'withdrawn' && p.kind !== 'preview'}
												<Button variant="ghost" size="sm" class="size-8 p-0" disabled={working} title="Withdraw (keeps submitted data)" aria-label="Withdraw {p.label}" onclick={() => ask('withdraw', p)}>
													<UserX />
												</Button>
											{/if}
											<Button variant="ghost" size="sm" class="size-8 p-0 text-danger-fg" disabled={working} title="Delete with all data" aria-label="Delete {p.label}" onclick={() => ask('delete', p)}>
												<Trash2 />
											</Button>
										</span>
									</div>
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<PaginationBar page={pageNo} {pages} onGoToPage={(n) => (pageNo = n)} class="border-t border-border" scrollToCard />
		{/if}
	</Card.Root>
</div>

<CodesDialog bind:open={codesOpen} {task} oncreated={refreshAll} />
<AccountsDialog bind:open={accountsOpen} {task} oncreated={refreshAll} />
<ExclusionDialog
	bind:open={exclusionOpen}
	subject={exclusionTarget?.label ?? 'participant'}
	excluded={exclusionTarget?.excluded ?? false}
	reason={exclusionTarget?.exclusion_reason ?? ''}
	{postHoc}
	onsubmit={submitExclusion}
/>
<SecretDialog bind:open={secretOpen} title={secret.title} description={secret.description} value={secret.value} label={secret.label} note={secret.note} />
<ConfirmDialog
	bind:open={confirmOpen}
	title={confirmCopy.title}
	description={confirmCopy.description}
	confirmLabel={confirmCopy.label}
	destructive={confirmCopy.destructive}
	busy={confirmBusy}
	{onConfirm}
/>

<Dialog.Root bind:open={noteOpen}>
	<Dialog.Content class="max-w-md">
		<Dialog.Header>
			<Dialog.Title>Note for {noteTarget?.label}</Dialog.Title>
			<Dialog.Description>Only researchers see it.</Dialog.Description>
		</Dialog.Header>
		<form class="space-y-4" onsubmit={saveNote}>
			<div class="space-y-1.5">
				<Label for="he-participant-note" class="sr-only">Note</Label>
				<Textarea id="he-participant-note" rows={4} bind:value={noteText} />
			</div>
			<Dialog.Footer>
				<Button variant="outline" size="sm" type="button" disabled={noteSaving} onclick={() => (noteOpen = false)}>Cancel</Button>
				<Button size="sm" type="submit" disabled={noteSaving} aria-busy={noteSaving}>Save note</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>
