<script lang="ts">
	/**
	 * AccessCodeForm — "Enter access code": resumes a link / code session
	 * (POST portal/resume/), reloads the portal session and opens the task page
	 * (or calls `onresumed`). The input upper-cases the code and groups it in
	 * fours while typing; dashes and spaces are optional. Input that cannot be a
	 * code (wrong length, characters outside the code alphabet) is stopped before
	 * it reaches the server, because failed attempts count against a throttle a
	 * whole room may share. Unknown codes get the server's generic message, too
	 * many failed attempts (429) a "wait" message.
	 *
	 * Props: `onresumed(result)` (default: go to the task), `autofocus`,
	 * `submitLabel` ("Continue"), `class`.
	 */
	import { tick } from 'svelte';
	import { goto } from '$app/navigation';
	import ArrowRight from '@lucide/svelte/icons/arrow-right';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import { resumeWithCode } from '$lib/api/human-eval-portal';
	import type { ResumeResult } from '$lib/api/types/human-eval';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { cn } from '$lib/utils';
	import { getPortalSession } from './portal-session.svelte';
	import {
		accessCodeProblem,
		caretAfterFormat,
		formatAccessCodeInput,
		invalidAccessCodeChars,
		normalizeAccessCode,
		portalErrorMessage,
		taskPath,
	} from './portal';

	interface Props {
		onresumed?: (result: ResumeResult) => void | Promise<void>;
		autofocus?: boolean;
		submitLabel?: string;
		class?: string;
	}

	let { onresumed, autofocus = false, submitLabel = 'Continue', class: className }: Props = $props();

	const session = getPortalSession();
	const uid = $props.id();
	const inputId = `access-code-${uid}`;

	let code = $state('');
	let busy = $state(false);
	let error = $state('');

	async function onInput(event: Event) {
		const el = event.currentTarget as HTMLInputElement;
		const raw = el.value;
		const caret = el.selectionStart ?? raw.length;
		const formatted = formatAccessCodeInput(raw);
		code = formatted;
		// Characters that never occur in a code are flagged while typing; length on submit.
		error = invalidAccessCodeChars(formatted).length ? accessCodeProblem(formatted) : '';
		if (el.value !== formatted) {
			el.value = formatted;
			await tick();
			const pos = caretAfterFormat(raw, caret, formatted);
			try {
				el.setSelectionRange(pos, pos);
			} catch {
				// Some input types do not support selection; ours is text.
			}
		}
	}

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		const problem = accessCodeProblem(code);
		if (problem) {
			error = problem;
			return;
		}
		busy = true;
		error = '';
		try {
			const result = await resumeWithCode({ code: normalizeAccessCode(code) });
			await session.refresh();
			if (onresumed) await onresumed(result);
			else await goto(taskPath(result.task_id));
		} catch (e) {
			error = portalErrorMessage(e, 'Could not check the code.');
		} finally {
			busy = false;
		}
	}
</script>

<form class={cn('space-y-2', className)} onsubmit={submit} novalidate>
	<label for={inputId} class="text-sm font-medium">Access code</label>
	<div class="flex flex-col gap-2 sm:flex-row">
		<!-- svelte-ignore a11y_autofocus -->
		<Input
			id={inputId}
			name="access-code"
			value={code}
			oninput={onInput}
			autocomplete="off"
			autocapitalize="characters"
			spellcheck={false}
			{autofocus}
			placeholder="XXXX-XXXX-XXXX"
			class="h-10 font-mono text-base uppercase tracking-[0.12em] placeholder:tracking-[0.12em] sm:max-w-xs"
			aria-invalid={error ? true : undefined}
			aria-describedby="{inputId}-hint{error ? ` ${inputId}-error` : ''}"
		/>
		<Button type="submit" class="h-10" disabled={busy} aria-busy={busy}>
			{#if busy}
				<LoaderCircle class="animate-spin motion-reduce:animate-none" aria-hidden="true" />
			{/if}
			{submitLabel}
			{#if !busy}<ArrowRight aria-hidden="true" />{/if}
		</Button>
	</div>
	<p id="{inputId}-hint" class="text-xs text-muted-foreground">
		The 12-character code you were given or wrote down when you joined. Dashes and spaces are optional.
	</p>
	{#if error}
		<p id="{inputId}-error" class="text-sm font-medium text-danger-fg" role="alert">{error}</p>
	{/if}
</form>
