<script lang="ts">
	/**
	 * CodesDialog — generate access codes (1..500), then show them ONCE:
	 * copy all, download a CSV, or print cut-out slips. The plaintext codes are
	 * dropped when the dialog closes (only hashes are stored server-side).
	 *
	 * Props: `open` (bindable), `task`, `oncreated()` (refresh the participant list).
	 */
	import { toast } from 'svelte-sonner';
	import * as Dialog from '$lib/components/ui/dialog';
	import { Alert, AlertDescription } from '$lib/components/ui/alert';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { formatApiError } from '$lib/api/core';
	import { createAccessCodes, humanEvalErrorBody, type CreatedCode, type TaskDetail } from '$lib/api/human-eval';
	import { absoluteUrl, codesCsv, codesPrintHtml, codesText, plural, slugify } from '../format';
	import { copyText, downloadText, printHtml } from './browser';
	import Copy from '@lucide/svelte/icons/copy';
	import Download from '@lucide/svelte/icons/download';
	import KeyRound from '@lucide/svelte/icons/key-round';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Printer from '@lucide/svelte/icons/printer';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';

	interface Props {
		open: boolean;
		task: TaskDetail;
		oncreated?: () => void;
	}

	let { open = $bindable(false), task, oncreated }: Props = $props();

	let countText = $state('10');
	let creating = $state(false);
	let codes = $state.raw<CreatedCode[] | null>(null);
	let error = $state('');

	const count = $derived(Number.parseInt(countText, 10));
	const countValid = $derived(Number.isInteger(count) && count >= 1 && count <= 500);
	const portalUrl = $derived(typeof window === 'undefined' ? '/eval' : absoluteUrl(window.location.origin, '/eval'));

	// Plaintext codes live only while the dialog is open.
	$effect(() => {
		if (!open) {
			codes = null;
			error = '';
		}
	});

	async function create(e: SubmitEvent) {
		e.preventDefault();
		if (!countValid) return;
		creating = true;
		error = '';
		try {
			const res = await createAccessCodes(task.id, count);
			codes = res.codes;
			toast.success(`Created ${plural(res.codes.length, 'access code')}`);
			oncreated?.();
		} catch (err) {
			const body = humanEvalErrorBody(err);
			error = body?.code === 'access_disabled' ? 'Access codes are turned off in this task’s settings (Design → Access).' : (body?.detail ?? formatApiError(err, 'Could not create the codes.'));
		} finally {
			creating = false;
		}
	}

	function download() {
		if (!codes) return;
		downloadText(`human-eval-${slugify(task.title)}-access-codes.csv`, codesCsv(codes, portalUrl));
	}

	function print() {
		if (!codes) return;
		printHtml(codesPrintHtml(codes, { title: task.title, portalUrl }));
	}
</script>

<Dialog.Root bind:open>
	<Dialog.Content class="max-h-[90vh] max-w-lg overflow-y-auto">
		<Dialog.Header>
			<Dialog.Title>{codes ? 'Access codes' : 'Generate access codes'}</Dialog.Title>
			<Dialog.Description>
				{codes
					? `Hand one code to each participant. They enter it at ${portalUrl}.`
					: 'Each code creates a participant who signs in at the portal with it. Codes are stored only as hashes.'}
			</Dialog.Description>
		</Dialog.Header>

		{#if codes}
			<Alert class="border-warning/40 bg-warning/5">
				<TriangleAlert class="text-warning-fg" />
				<AlertDescription>
					These codes are shown only now. Copy, download or print them before closing; a lost code can be replaced per participant
					(“New code”).
				</AlertDescription>
			</Alert>
			<div class="flex flex-wrap gap-2">
				<Button variant="outline" size="sm" onclick={() => copyText(codesText(codes ?? []), `Copied ${plural(codes?.length ?? 0, 'code')}`)}>
					<Copy />Copy all
				</Button>
				<Button variant="outline" size="sm" onclick={download}><Download />Download CSV</Button>
				<Button variant="outline" size="sm" onclick={print}><Printer />Print slips</Button>
			</div>
			<div class="max-h-80 overflow-y-auto rounded-md border border-border">
				<table>
					<thead><tr><th>Participant</th><th>Access code</th></tr></thead>
					<tbody>
						{#each codes as c (c.participant_id)}
							<tr>
								<td class="font-mono text-xs">{c.label}</td>
								<td><code class="select-all font-mono text-sm tracking-wider">{c.code}</code></td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<Dialog.Footer>
				<Button size="sm" onclick={() => (open = false)}>Done</Button>
			</Dialog.Footer>
		{:else}
			<form class="space-y-4" onsubmit={create}>
				<div class="space-y-1.5">
					<Label for="he-code-count">Number of codes</Label>
					<Input id="he-code-count" type="number" inputmode="numeric" min="1" max="500" step="1" bind:value={countText} class="w-32" aria-invalid={!countValid || undefined} />
					<p class="text-xs text-muted-foreground">1–500 per batch. Each code is one participant (P-number).</p>
				</div>
				{#if error}<p class="text-sm text-danger-fg" role="alert">{error}</p>{/if}
				<Dialog.Footer>
					<Button variant="outline" size="sm" type="button" disabled={creating} onclick={() => (open = false)}>Cancel</Button>
					<Button size="sm" type="submit" disabled={!countValid || creating} aria-busy={creating}>
						{#if creating}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{:else}<KeyRound />{/if}
						Generate {countValid ? plural(count, 'code') : 'codes'}
					</Button>
				</Dialog.Footer>
			</form>
		{/if}
	</Dialog.Content>
</Dialog.Root>
