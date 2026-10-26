<script lang="ts">
	/**
	 * LaunchPanel — the last wizard step (and the locked study's call to
	 * action): lock the protocol (freezing plan, targets and design under a
	 * sha256), review its summary, then launch with an explicit cost
	 * confirmation. Unlocking is possible until launch.
	 *
	 * Props: `study` (detail), `onchange(study)` after lock/unlock,
	 * `onlaunched(study)` after a successful (202) launch, `onrefresh()`
	 * after a refused lock (the server stored a fresh preflight),
	 * `onpreflight()` (offers "Review the preflight" next to blocking problems).
	 */
	import {
		isPreflightReport,
		launchStudy,
		lockStudy,
		studyBlockingIssues,
		studyErrorCode,
		unlockStudy,
		type PreflightIssue,
		type StudyDetail
	} from '$lib/api/analysis';
	import { downloadStudyProtocolMarkdown } from '$lib/api/analysis';
	import { formatApiError } from '$lib/api/core';
	import { toast } from 'svelte-sonner';
	import * as Card from '$lib/components/ui/card';
	import * as Dialog from '$lib/components/ui/dialog';
	import { Button } from '$lib/components/ui/button';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import Lock from '@lucide/svelte/icons/lock';
	import LockOpen from '@lucide/svelte/icons/lock-open';
	import Rocket from '@lucide/svelte/icons/rocket';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import FileDown from '@lucide/svelte/icons/file-down';
	import ShieldCheck from '@lucide/svelte/icons/shield-check';
	import ShieldAlert from '@lucide/svelte/icons/shield-alert';
	import IssueList from './IssueList.svelte';
	import { fmtDateTime, fmtInt, fmtSeconds, fmtUsd, profileLabel, shortHash } from './format';

	interface Props {
		study: StudyDetail;
		onchange?: (study: StudyDetail) => void;
		onlaunched?: (study: StudyDetail) => void;
		onrefresh?: () => void;
		onpreflight?: () => void;
	}

	let { study, onchange, onlaunched, onrefresh, onpreflight }: Props = $props();

	let busy = $state<'' | 'lock' | 'unlock' | 'launch' | 'download'>('');
	let error = $state('');
	let blocking = $state<PreflightIssue[]>([]);
	let confirmOpen = $state(false);

	const preflight = $derived(isPreflightReport(study.preflight) ? study.preflight : null);
	const est = $derived(preflight?.estimates ?? null);
	const reviewer = $derived(preflight?.reviewer ?? null);
	const summary = $derived(study.preflight_summary);
	const locked = $derived(study.status === 'locked');
	const canLock = $derived(study.actions.lock && (!summary || summary.blocking === 0));

	const HINTS: Record<string, string> = {
		image_changed:
			'The analyzer image changed since the lock. Unlock and lock again to freeze the new tool versions.',
		protocol_integrity: 'The stored protocol no longer matches its hash. Unlock and lock again.',
		plan_integrity: 'The frozen plan no longer matches its hash. Unlock and lock again.',
		nothing_to_launch:
			'The protocol has no eligible target. Unlock and change the targets or the exclusion rules.'
	};

	async function lock() {
		busy = 'lock';
		error = '';
		blocking = [];
		try {
			const next = await lockStudy(study.id);
			toast.success('Protocol locked');
			onchange?.(next);
		} catch (e) {
			blocking = studyBlockingIssues(e);
			error = formatApiError(e, 'Could not lock the protocol.');
			// A refused lock still stores the fresh preflight: show its numbers.
			if (blocking.length > 0) onrefresh?.();
		} finally {
			busy = '';
		}
	}

	async function unlock() {
		busy = 'unlock';
		error = '';
		try {
			const next = await unlockStudy(study.id);
			toast.success('Back to draft: the frozen protocol was discarded');
			onchange?.(next);
		} catch (e) {
			error = formatApiError(e, 'Could not unlock the study.');
		} finally {
			busy = '';
		}
	}

	async function launch() {
		busy = 'launch';
		error = '';
		try {
			const next = await launchStudy(study.id);
			confirmOpen = false;
			toast.success('Launched: runs are being submitted');
			onlaunched?.(next);
		} catch (e) {
			const code = studyErrorCode(e);
			error = [formatApiError(e, 'Could not launch the study.'), code ? HINTS[code] : '']
				.filter(Boolean)
				.join(' ');
			confirmOpen = false;
		} finally {
			busy = '';
		}
	}

	async function downloadProtocol() {
		busy = 'download';
		try {
			await downloadStudyProtocolMarkdown(study.id);
		} catch (e) {
			toast.error(formatApiError(e, 'Could not download the protocol.'));
		} finally {
			busy = '';
		}
	}
</script>

<div class="space-y-4">
	{#if error}<ErrorAlert message={error} />{/if}
	{#if blocking.length > 0}
		<IssueList
			issues={blocking}
			tone="danger"
			title="Blocking problems (fix them, then re-run the preflight)"
		/>
		{#if onpreflight}
			<Button variant="outline" size="sm" onclick={onpreflight}>Review the preflight</Button>
		{/if}
	{/if}

	{#if study.status === 'draft'}
		<Card.Root>
			<Card.Header>
				<Card.Title>Lock the protocol</Card.Title>
				<Card.Description>
					Locking freezes the analysis plan (tool versions from the image, configs, AI reviewer and
					model price), the resolved targets, exclusion rules and analysis design into a protocol
					with a sha256. It runs a fresh preflight and is refused while anything blocks. You can
					unlock again until launch.
				</Card.Description>
			</Card.Header>
			<Card.Content class="space-y-3">
				{#if summary}
					<p class="text-sm">
						Last preflight {fmtDateTime(summary.generated_at)}:
						<span class={summary.blocking > 0 ? 'font-medium text-danger-fg' : 'text-success-fg'}
							>{summary.blocking} blocking</span
						>
						· {summary.warnings} warning{summary.warnings === 1 ? '' : 's'} · {summary.eligible ??
							'—'} of {summary.targets ?? '—'}
						targets eligible.
					</p>
				{:else}
					<p class="text-sm text-muted-foreground">No preflight yet: locking runs one first.</p>
				{/if}
				<Button onclick={lock} disabled={!canLock || busy !== ''}>
					{#if busy === 'lock'}<LoaderCircle
							class="animate-spin motion-reduce:animate-none"
						/>{:else}<Lock />{/if}
					Lock protocol
				</Button>
			</Card.Content>
		</Card.Root>
	{:else if locked}
		<Card.Root>
			<Card.Header class="flex flex-row flex-wrap items-start justify-between gap-2">
				<div class="space-y-1">
					<Card.Title class="flex items-center gap-2">
						{#if study.protocol_verified === false}
							<ShieldAlert class="h-4 w-4 text-danger-fg" aria-hidden="true" />
						{:else}
							<ShieldCheck class="h-4 w-4 text-success-fg" aria-hidden="true" />
						{/if}
						Protocol locked
					</Card.Title>
					<Card.Description>
						Locked {fmtDateTime(study.locked_at)} · sha256
						<code class="font-mono text-foreground" title={study.protocol_hash}
							>{shortHash(study.protocol_hash, 16)}</code
						>
						{#if study.protocol_verified === false}<StatusBadge
								status="failed"
								label="hash mismatch"
							/>{/if}
					</Card.Description>
				</div>
				<div class="flex flex-wrap gap-2">
					<Button variant="outline" size="sm" href="/analysis/studies/{study.id}?tab=protocol"
						>Read protocol</Button
					>
					<Button variant="outline" size="sm" onclick={downloadProtocol} disabled={busy !== ''}>
						{#if busy === 'download'}<LoaderCircle
								class="animate-spin motion-reduce:animate-none"
							/>{:else}<FileDown />{/if}
						protocol.md
					</Button>
				</div>
			</Card.Header>
			<Card.Content class="space-y-4">
				<dl class="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2 lg:grid-cols-4">
					<div>
						<dt class="section-label">Targets</dt>
						<dd>
							{fmtInt(est?.eligible ?? summary?.eligible)} runs (of {fmtInt(
								est?.targets ?? summary?.targets
							)})
						</dd>
					</div>
					<div>
						<dt class="section-label">Profile</dt>
						<dd>{profileLabel(study)}</dd>
					</div>
					<div>
						<dt class="section-label">Plan</dt>
						<dd class="font-mono text-xs" title={study.plan_hash}>
							#{shortHash(study.plan_hash)}
						</dd>
					</div>
					<div>
						<dt class="section-label">AI reviewer</dt>
						<dd>
							{#if reviewer?.enabled}{reviewer.model} · k={reviewer.samples}{:else}none{/if}
						</dd>
					</div>
					<div>
						<dt class="section-label">Tool executions</dt>
						<dd>{fmtInt(est?.tool_executions)}</dd>
					</div>
					<div>
						<dt class="section-label">AI calls</dt>
						<dd>{fmtInt(est?.ai_calls)}</dd>
					</div>
					<div>
						<dt class="section-label">Est. AI cost</dt>
						<dd>
							{fmtUsd(est?.ai_cost_usd)}
							<span class="text-xs text-muted-foreground"
								>({fmtUsd(est?.ai_cost_usd_low)} – {fmtUsd(est?.ai_cost_usd_high)})</span
							>
						</dd>
					</div>
					<div>
						<dt class="section-label">Est. wall time</dt>
						<dd>{fmtSeconds(est?.wall_seconds)}</dd>
					</div>
				</dl>
				<div class="flex flex-wrap justify-end gap-2">
					{#if study.actions.unlock}
						<Button variant="outline" size="sm" onclick={unlock} disabled={busy !== ''}>
							{#if busy === 'unlock'}<LoaderCircle
									class="animate-spin motion-reduce:animate-none"
								/>{:else}<LockOpen />{/if}
							Unlock
						</Button>
					{/if}
					<Button
						size="sm"
						onclick={() => (confirmOpen = true)}
						disabled={!study.actions.launch || busy !== ''}
					>
						<Rocket />Launch study
					</Button>
				</div>
			</Card.Content>
		</Card.Root>
	{/if}
</div>

<Dialog.Root bind:open={confirmOpen}>
	<Dialog.Content class="max-w-lg space-y-4">
		<Dialog.Header>
			<Dialog.Title>Launch {study.name}?</Dialog.Title>
			<Dialog.Description class="text-sm text-muted-foreground">
				One run per eligible target is submitted under the locked protocol
				<code class="font-mono">#{shortHash(study.protocol_hash)}</code>. Cancelling later stops the
				remaining runs; the launch itself cannot be undone.
			</Dialog.Description>
		</Dialog.Header>
		<dl
			class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 rounded-md border border-border bg-muted/40 px-3 py-2 text-sm"
		>
			<dt class="text-muted-foreground">Runs</dt>
			<dd class="tabular-nums">{fmtInt(est?.eligible ?? summary?.eligible)}</dd>
			<dt class="text-muted-foreground">Tool executions</dt>
			<dd class="tabular-nums">{fmtInt(est?.tool_executions)}</dd>
			{#if reviewer?.enabled}
				<dt class="text-muted-foreground">AI review calls</dt>
				<dd class="tabular-nums">
					{fmtInt(est?.ai_calls)} ({reviewer.model}, k = {reviewer.samples})
				</dd>
				<dt class="text-muted-foreground">Expected AI cost</dt>
				<dd class="font-semibold tabular-nums">
					{fmtUsd(est?.ai_cost_usd)}
					<span class="font-normal text-muted-foreground"
						>range {fmtUsd(est?.ai_cost_usd_low)} – {fmtUsd(est?.ai_cost_usd_high)}</span
					>
				</dd>
				<dt class="text-muted-foreground">Budget cap</dt>
				<dd class="text-xs">
					{fmtUsd(reviewer.budget.max_cost_usd ?? null)} per review sample · paid with your OpenRouter
					key
				</dd>
			{/if}
			<dt class="text-muted-foreground">Est. wall time</dt>
			<dd>{fmtSeconds(est?.wall_seconds)}</dd>
		</dl>
		<Dialog.Footer>
			<Button
				variant="outline"
				size="sm"
				onclick={() => (confirmOpen = false)}
				disabled={busy === 'launch'}>Cancel</Button
			>
			<Button size="sm" onclick={launch} disabled={busy === 'launch'}>
				{#if busy === 'launch'}<LoaderCircle
						class="animate-spin motion-reduce:animate-none"
					/>{:else}<Rocket />{/if}
				Launch {fmtInt(est?.eligible ?? summary?.eligible)} runs
			</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>
