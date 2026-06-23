<script lang="ts">
import * as Dialog from '$lib/components/ui/dialog';
import { Button } from '$lib/components/ui/button';
import LoaderCircle from '@lucide/svelte/icons/loader-circle';

interface Props {
	open: boolean;
	title: string;
	description?: string;
	confirmLabel?: string;
	cancelLabel?: string;
	destructive?: boolean;
	busy?: boolean;
	onConfirm: () => void;
}

let {
	open = $bindable(false),
	title,
	description = '',
	confirmLabel = 'Confirm',
	cancelLabel = 'Cancel',
	destructive = false,
	busy = false,
	onConfirm,
}: Props = $props();
</script>

<Dialog.Root bind:open>
	<Dialog.Content class="max-w-md">
		<Dialog.Header>
			<Dialog.Title>{title}</Dialog.Title>
			{#if description}
				<Dialog.Description>{description}</Dialog.Description>
			{/if}
		</Dialog.Header>
		<Dialog.Footer>
			<Button variant="outline" size="sm" disabled={busy} onclick={() => (open = false)}>
				{cancelLabel}
			</Button>
			<Button
				variant={destructive ? 'destructive' : 'default'}
				size="sm"
				disabled={busy}
				onclick={onConfirm}
			>
				{#if busy}
					<LoaderCircle class="mr-1.5 h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />
				{/if}
				{confirmLabel}
			</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>
