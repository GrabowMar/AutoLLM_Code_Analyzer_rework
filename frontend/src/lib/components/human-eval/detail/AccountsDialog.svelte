<script lang="ts">
	/**
	 * AccountsDialog — add participants by email. Unknown emails become new
	 * restricted evaluator accounts (portal only) unless "only link existing
	 * accounts" is chosen; existing users are linked. Results list every email:
	 * created / linked / error, with a copyable set-password link for new
	 * accounts (full URL = this site's origin + the server's path).
	 *
	 * Props: `open` (bindable), `task`, `oncreated()`.
	 */
	import { toast } from 'svelte-sonner';
	import * as Dialog from '$lib/components/ui/dialog';
	import { Alert, AlertDescription } from '$lib/components/ui/alert';
	import { Button } from '$lib/components/ui/button';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Label } from '$lib/components/ui/label';
	import { Textarea } from '$lib/components/ui/textarea';
	import { formatApiError } from '$lib/api/core';
	import { addAccounts, humanEvalErrorBody, type AccountResult, type TaskDetail } from '$lib/api/human-eval';
	import { MAX_EMAILS_PER_CALL, absoluteUrl, parseEmails, plural } from '../format';
	import { copyText } from './browser';
	import Copy from '@lucide/svelte/icons/copy';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Mail from '@lucide/svelte/icons/mail';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';

	interface Props {
		open: boolean;
		task: TaskDetail;
		oncreated?: () => void;
	}

	let { open = $bindable(false), task, oncreated }: Props = $props();

	let text = $state('');
	let createMissing = $state(true);
	let sendEmail = $state(false);
	let submitting = $state(false);
	let error = $state('');
	let results = $state.raw<AccountResult[] | null>(null);

	const parsed = $derived(parseEmails(text));
	const tooMany = $derived(parsed.emails.length > MAX_EMAILS_PER_CALL);
	const origin = $derived(typeof window === 'undefined' ? '' : window.location.origin);
	const setupLinks = $derived((results ?? []).filter((r) => r.setup_path));

	$effect(() => {
		if (!open) {
			results = null;
			error = '';
		}
	});

	async function submit(e: SubmitEvent) {
		e.preventDefault();
		if (parsed.emails.length === 0 || tooMany) return;
		submitting = true;
		error = '';
		try {
			const res = await addAccounts(task.id, { emails: parsed.emails, restricted: createMissing, send_email: sendEmail });
			results = res.results;
			const ok = res.results.filter((r) => !r.error).length;
			const failed = res.results.length - ok;
			if (ok) toast.success(`Added ${plural(ok, 'account participant')}`);
			if (failed) toast.warning(`${plural(failed, 'email')} could not be added`);
			text = '';
			oncreated?.();
		} catch (err) {
			const body = humanEvalErrorBody(err);
			if (body?.code === 'access_disabled') error = 'Accounts are turned off in this task’s settings (Design → Access).';
			else if (body?.code === 'quota_exceeded') error = body.detail || 'Daily limit reached for new accounts or emails. Try again tomorrow.';
			else error = body?.detail ?? formatApiError(err, 'Could not add the accounts.');
		} finally {
			submitting = false;
		}
	}

	function allLinksText(): string {
		return setupLinks.map((r) => `${r.email}\t${absoluteUrl(origin, r.setup_path ?? '')}`).join('\n');
	}
</script>

<Dialog.Root bind:open>
	<Dialog.Content class="max-h-[90vh] max-w-2xl overflow-y-auto">
		<Dialog.Header>
			<Dialog.Title>{results ? 'Accounts added' : 'Add account participants'}</Dialog.Title>
			<Dialog.Description>
				{results
					? 'Send each new account its set-password link. Links expire after a while or once the account signs in; reissue them from the participant row.'
					: 'Participants sign in with a platform account and only see the evaluation portal.'}
			</Dialog.Description>
		</Dialog.Header>

		{#if results}
			{#if setupLinks.length > 0}
				<Alert class="border-warning/40 bg-warning/5">
					<TriangleAlert class="text-warning-fg" />
					<AlertDescription>
						Set-password links work like passwords until used: share each one only with its owner.
					</AlertDescription>
				</Alert>
				{#if setupLinks.length > 1}
					<Button variant="outline" size="sm" onclick={() => copyText(allLinksText(), `Copied ${plural(setupLinks.length, 'link')}`)}>
						<Copy />Copy all links
					</Button>
				{/if}
			{/if}
			<div class="max-h-96 overflow-auto rounded-md border border-border">
				<table>
					<thead><tr><th>Email</th><th>Result</th><th>Set-password link</th></tr></thead>
					<tbody>
						{#each results as r (r.email)}
							<tr>
								<td class="max-w-56 truncate text-xs" title={r.email}>{r.email}</td>
								<td class="text-xs">
									{#if r.error}
										<span class="text-danger-fg">{r.error}</span>
									{:else if r.user_created}
										New evaluator account{r.participant ? ` · ${r.participant.label}` : ''}
									{:else}
										Linked existing account{r.participant ? ` · ${r.participant.label}` : ''}
									{/if}
								</td>
								<td>
									{#if r.setup_path}
										{@const url = absoluteUrl(origin, r.setup_path)}
										<div class="flex min-w-0 items-center gap-1">
											<code class="min-w-0 max-w-64 truncate font-mono text-[11px]" title={url}>{url}</code>
											<Button variant="ghost" size="sm" class="size-7 p-0" aria-label="Copy the link for {r.email}" onclick={() => copyText(url, 'Link copied')}>
												<Copy />
											</Button>
										</div>
									{:else}
										<span class="text-xs text-muted-foreground">—</span>
									{/if}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<Dialog.Footer>
				<Button variant="outline" size="sm" onclick={() => (results = null)}>Add more</Button>
				<Button size="sm" onclick={() => (open = false)}>Done</Button>
			</Dialog.Footer>
		{:else}
			<form class="space-y-4" onsubmit={submit}>
				<div class="space-y-1.5">
					<Label for="he-account-emails">Emails</Label>
					<Textarea id="he-account-emails" rows={6} bind:value={text} placeholder={'ada@example.com\nGrace Hopper <grace@example.com>'} />
					<p class="text-xs text-muted-foreground">
						One per line or separated by commas.
						{#if parsed.emails.length}<span class="text-foreground">{plural(parsed.emails.length, 'address', 'addresses')}</span>{/if}
						{#if tooMany}<span class="text-danger-fg"> — at most {MAX_EMAILS_PER_CALL} per batch.</span>{/if}
					</p>
					{#if parsed.invalid.length}
						<p class="text-xs text-danger-fg">Not an email: {parsed.invalid.slice(0, 5).join(', ')}{parsed.invalid.length > 5 ? ` and ${parsed.invalid.length - 5} more` : ''}</p>
					{/if}
				</div>
				<fieldset class="space-y-2.5">
					<legend class="sr-only">Options</legend>
					<label class="flex items-start gap-2.5 text-sm">
						<Checkbox bind:checked={createMissing} class="mt-0.5" />
						<span>
							Create accounts for unknown emails
							<span class="block text-xs text-muted-foreground">
								New accounts are restricted evaluators: they can only use the evaluation portal. Off: only link people who already have
								an account (unknown emails are reported).
							</span>
						</span>
					</label>
					<label class="flex items-start gap-2.5 text-sm">
						<Checkbox bind:checked={sendEmail} class="mt-0.5" />
						<span>
							Email the links
							<span class="block text-xs text-muted-foreground">New accounts get their set-password link and the portal address by email.</span>
						</span>
					</label>
				</fieldset>
				{#if !task.settings.access.allow_accounts}
					<p class="text-sm text-warning-fg">Accounts are turned off in this task’s settings; enable them under Design → Access first.</p>
				{/if}
				{#if error}<p class="text-sm text-danger-fg" role="alert">{error}</p>{/if}
				<Dialog.Footer>
					<Button variant="outline" size="sm" type="button" disabled={submitting} onclick={() => (open = false)}>Cancel</Button>
					<Button size="sm" type="submit" disabled={submitting || parsed.emails.length === 0 || tooMany} aria-busy={submitting}>
						{#if submitting}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{:else}<Mail />{/if}
						Add {parsed.emails.length ? plural(parsed.emails.length, 'participant') : 'participants'}
					</Button>
				</Dialog.Footer>
			</form>
		{/if}
	</Dialog.Content>
</Dialog.Root>
