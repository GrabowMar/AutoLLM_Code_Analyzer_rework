<script lang="ts">
	/**
	 * ConsentEditor — informed-consent step: on/off and the Markdown text
	 * participants agree to before joining (links) or before their first item.
	 *
	 * Props: `id`, `consent` (bindable), `errors` (relative to consent), `readOnly`.
	 */
	import { Checkbox } from '$lib/components/ui/checkbox';
	import type { ConsentConfig } from '$lib/api/types/human-eval';
	import { messageAt, messagesAt, type FieldError } from './errors';
	import MarkdownField from './MarkdownField.svelte';

	interface Props {
		id: string;
		consent: ConsentConfig;
		errors?: FieldError[];
		readOnly?: boolean;
	}

	let { id, consent = $bindable(), errors = [], readOnly = false }: Props = $props();
</script>

<div class="space-y-3">
	<label class="flex items-start gap-2.5 text-sm">
		<Checkbox class="mt-0.5" bind:checked={consent.enabled} disabled={readOnly} />
		<span>
			<span class="font-medium">Ask for informed consent</span>
			<span class="block text-xs text-muted-foreground">
				Participants must agree to the text below before they join (links) or rate their first app; the time of consent is recorded.
			</span>
		</span>
	</label>
	{#if consent.enabled}
		<MarkdownField
			id="{id}-text"
			label="Consent text (Markdown)"
			bind:value={consent.text}
			rows={8}
			{readOnly}
			placeholder="What the study is, what is recorded, how data is stored and that participation is voluntary…"
			error={messageAt(errors, 'text')}
		/>
	{/if}
	{#each [...messagesAt(errors), ...messagesAt(errors, 'enabled')] as message (message)}
		<p class="text-[11px] text-danger-fg">{message}</p>
	{/each}
</div>
