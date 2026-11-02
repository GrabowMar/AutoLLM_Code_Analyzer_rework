<script lang="ts">
	/**
	 * SecretDialog — shows a one-time secret (a replacement access code, a
	 * set-password link) with Copy, and a note on how to hand it over.
	 *
	 * Props: `open` (bindable), `title`, `description`, `value`, `label`
	 * (accessible name of the value), `note` (muted line under it).
	 */
	import * as Dialog from '$lib/components/ui/dialog';
	import { Button } from '$lib/components/ui/button';
	import CopyField from '$lib/components/users-settings/CopyField.svelte';

	interface Props {
		open: boolean;
		title: string;
		description?: string;
		value: string;
		label: string;
		note?: string;
	}

	let { open = $bindable(false), title, description = '', value, label, note = '' }: Props = $props();
</script>

<Dialog.Root bind:open>
	<Dialog.Content class="max-w-lg">
		<Dialog.Header>
			<Dialog.Title>{title}</Dialog.Title>
			{#if description}<Dialog.Description>{description}</Dialog.Description>{/if}
		</Dialog.Header>
		<div class="space-y-2 py-1">
			<CopyField {value} {label} />
			{#if note}<p class="text-xs text-muted-foreground">{note}</p>{/if}
		</div>
		<Dialog.Footer>
			<Button size="sm" onclick={() => (open = false)}>Done</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>
