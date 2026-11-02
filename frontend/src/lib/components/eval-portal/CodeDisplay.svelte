<script lang="ts">
	/**
	 * CodeDisplay — a code the participant must keep (the resume / access code
	 * after joining, the completion code after finishing): large mono text, a
	 * copy button and, optionally, "Save as text file". Screen readers get the
	 * code spelled out character by character.
	 *
	 * Props:
	 * - `code`: shown exactly as given (`grouped` re-groups it in fours).
	 * - `label`: what the code is ("Your access code").
	 * - `grouped`: normalize and group an access code (default false).
	 * - `download`: `{ filename, text }` for the save button (optional).
	 * - `class`.
	 */
	import Check from '@lucide/svelte/icons/check';
	import Copy from '@lucide/svelte/icons/copy';
	import Download from '@lucide/svelte/icons/download';
	import { toast } from 'svelte-sonner';
	import { Button } from '$lib/components/ui/button';
	import { cn } from '$lib/utils';
	import { formatAccessCode } from './portal';

	interface Props {
		code: string;
		label: string;
		grouped?: boolean;
		download?: { filename: string; text: string } | null;
		class?: string;
	}

	let { code, label, grouped = false, download = null, class: className }: Props = $props();

	const shown = $derived(grouped ? formatAccessCode(code) : code);
	const parts = $derived(grouped ? shown.split('-') : [shown]);
	const spelled = $derived(
		shown
			.split('')
			.map((c) => (c === '-' ? ',' : c))
			.join(' ')
	);
	let copied = $state(false);
	let copyTimer: ReturnType<typeof setTimeout> | undefined;

	function legacyCopy(text: string): boolean {
		const area = document.createElement('textarea');
		area.value = text;
		area.setAttribute('readonly', '');
		area.style.position = 'fixed';
		area.style.opacity = '0';
		document.body.appendChild(area);
		area.select();
		let ok = false;
		try {
			ok = document.execCommand('copy');
		} catch {
			ok = false;
		}
		area.remove();
		return ok;
	}

	async function copy() {
		let ok = false;
		try {
			await navigator.clipboard.writeText(shown);
			ok = true;
		} catch {
			ok = legacyCopy(shown);
		}
		if (!ok) {
			toast.error('Could not copy. Select the code and copy it by hand.');
			return;
		}
		copied = true;
		clearTimeout(copyTimer);
		copyTimer = setTimeout(() => (copied = false), 1800);
	}

	function save() {
		if (!download) return;
		const blob = new Blob([download.text], { type: 'text/plain;charset=utf-8' });
		const url = URL.createObjectURL(blob);
		const a = document.createElement('a');
		a.href = url;
		a.download = download.filename;
		document.body.appendChild(a);
		a.click();
		a.remove();
		setTimeout(() => URL.revokeObjectURL(url), 1000);
	}
</script>

<div class={cn('space-y-3', className)}>
	<p class="section-label">{label}</p>
	<div class="rounded-md border border-primary/40 bg-primary/5 px-4 py-4 sm:px-6">
		<p
			class="flex select-all flex-wrap items-center justify-center gap-y-1 text-center font-mono text-2xl font-semibold tracking-[0.12em] text-foreground tabular-nums sm:text-4xl"
			aria-hidden="true"
		>
			{#each parts as part, i (i)}{#if i > 0}<span class="mx-0.5 text-muted-foreground sm:mx-1">-</span>{/if}<span
					class="whitespace-nowrap break-all">{part}</span
				>{/each}
		</p>
		<p class="sr-only">{label}: {spelled}</p>
	</div>
	<div class="flex flex-wrap items-center gap-2">
		<Button variant="outline" size="sm" onclick={copy}>
			{#if copied}
				<Check aria-hidden="true" />
				Copied
			{:else}
				<Copy aria-hidden="true" />
				Copy code
			{/if}
		</Button>
		{#if download}
			<Button variant="outline" size="sm" onclick={save}>
				<Download aria-hidden="true" />
				Save as text file
			</Button>
		{/if}
		<span class="sr-only" aria-live="polite">{copied ? 'Code copied to the clipboard' : ''}</span>
	</div>
</div>
