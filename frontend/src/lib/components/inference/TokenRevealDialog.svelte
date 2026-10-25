<script lang="ts">
	/**
	 * TokenRevealDialog — shows a relay token exactly once.
	 *
	 * The dialog can only be closed with "Done", which stays disabled until
	 * "I stored it" is ticked (Escape and outside clicks are ignored): the
	 * secret is never retrievable again.
	 *
	 * Props:
	 * - `open` (bindable).
	 * - `token`: `{id, prefix, secret_once}` from the create / rotate response.
	 * - `title` (default "Relay token"), `description`: what the token is for.
	 * - `note`: extra line under the box (e.g. the rotation grace period).
	 * - `onclose()`: after the user confirmed and closed; drop the secret here.
	 */
	import * as Dialog from '$lib/components/ui/dialog';
	import { Button } from '$lib/components/ui/button';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import type { TokenSecret } from '$lib/api/types/inference';
	import CopyBlock from './CopyBlock.svelte';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';

	interface Props {
		open?: boolean;
		token: TokenSecret | null;
		title?: string;
		description?: string;
		note?: string;
		onclose?: () => void;
	}

	let {
		open = $bindable(false),
		token,
		title = 'Relay token',
		description = 'The relay authenticates with this token. Store it on the relay machine now: it is shown only once.',
		note = '',
		onclose,
	}: Props = $props();

	let stored = $state(false);

	// A new token always starts unconfirmed.
	$effect(() => {
		void token?.id;
		stored = false;
	});

	function close() {
		if (!stored) return;
		open = false;
		onclose?.();
	}
</script>

<Dialog.Root
	{open}
	onOpenChange={(next) => {
		// Only "Done" closes; programmatic opens pass through.
		if (next) open = true;
	}}
>
	<Dialog.Content
		class="max-w-xl"
		showClose={false}
		escapeKeydownBehavior="ignore"
		interactOutsideBehavior="ignore"
	>
		<Dialog.Header>
			<Dialog.Title>{title}</Dialog.Title>
			<Dialog.Description>{description}</Dialog.Description>
		</Dialog.Header>
		{#if token}
			<div class="mt-4 space-y-3">
				<CopyBlock text={token.secret_once} label="relay token" wrap />
				<p class="flex items-start gap-1.5 text-xs text-warning-fg">
					<TriangleAlert class="mt-px size-3.5 shrink-0" aria-hidden="true" />
					<span>
						Paste it into the token file with the install command (it reads stdin, so the token never lands in
						your shell history). Never put it on a command line.
					</span>
				</p>
				{#if note}<p class="text-xs text-muted-foreground">{note}</p>{/if}
				<p class="text-[11px] text-muted-foreground">
					Token <code class="mono-tag">{token.prefix}…</code> — the prefix identifies it in the token list.
				</p>
				<label class="flex cursor-pointer items-center gap-2 text-sm">
					<Checkbox bind:checked={stored} />
					I stored the token
				</label>
			</div>
		{/if}
		<Dialog.Footer class="mt-4">
			<Button size="sm" disabled={!stored && !!token} onclick={() => (token ? close() : (open = false))}>Done</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>
