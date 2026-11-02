<script lang="ts">
	/**
	 * ConsentPanel — the study information / consent text (markdown, in a
	 * scrollable, keyboard-focusable box) and either the agreement checkbox or,
	 * once consent was given, when it was given.
	 *
	 * Props:
	 * - `text` (markdown from `design.consent.text`).
	 * - `agreed` (bindable boolean): the checkbox.
	 * - `consentedAt` (ISO time): consent already recorded — no checkbox.
	 * - `readOnly`: hide the checkbox (e.g. when joining is not possible).
	 * - `error`: message under the checkbox.
	 */
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import Markdown from './Markdown.svelte';
	import { formatDateTime } from './portal';

	interface Props {
		text: string;
		agreed?: boolean;
		consentedAt?: string | null;
		readOnly?: boolean;
		error?: string;
	}

	let { text, agreed = $bindable(false), consentedAt = null, readOnly = false, error = '' }: Props = $props();

	const uid = $props.id();
</script>

<div class="space-y-3">
	<!-- A scrollable region must be focusable so keyboard users can scroll it. -->
	<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
	<div
		class="max-h-[55vh] overflow-y-auto rounded-md border border-border bg-surface-1 p-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
		role="region"
		aria-label="Study information and consent"
		tabindex="0"
	>
		{#if text.trim()}
			<Markdown source={text} />
		{:else}
			<p class="text-sm text-muted-foreground">The researcher did not add any study information.</p>
		{/if}
	</div>

	{#if consentedAt}
		<p class="flex items-center gap-1.5 text-sm text-success-fg">
			<CircleCheck class="size-4 shrink-0" aria-hidden="true" />
			You gave your consent on {formatDateTime(consentedAt)}.
		</p>
	{:else if !readOnly}
		<label class="flex cursor-pointer items-start gap-2.5 text-sm" for="consent-{uid}">
			<Checkbox
				id="consent-{uid}"
				class="mt-0.5"
				bind:checked={agreed}
				aria-invalid={error ? true : undefined}
				aria-describedby={error ? `consent-${uid}-error` : undefined}
			/>
			<span>I have read the information above and agree to take part in this study.</span>
		</label>
		{#if error}
			<p id="consent-{uid}-error" class="text-[11px] font-medium text-danger-fg">{error}</p>
		{/if}
	{/if}
</div>
