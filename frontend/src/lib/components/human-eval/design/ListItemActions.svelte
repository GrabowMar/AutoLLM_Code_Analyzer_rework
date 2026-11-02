<script lang="ts">
	/**
	 * ListItemActions — move up / move down / (duplicate) / remove buttons for
	 * one entry of an editable list. `name` makes the accessible labels specific
	 * ("Move “Usability” up").
	 *
	 * Props: `name`, `index`, `count`, `onmove(delta)`, `onremove`, `onduplicate?`,
	 * `disabled`.
	 */
	import { Button } from '$lib/components/ui/button';
	import ChevronUp from '@lucide/svelte/icons/chevron-up';
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import Copy from '@lucide/svelte/icons/copy';
	import Trash2 from '@lucide/svelte/icons/trash-2';

	interface Props {
		name: string;
		index: number;
		count: number;
		onmove: (delta: number) => void;
		onremove: () => void;
		onduplicate?: () => void;
		disabled?: boolean;
	}

	let { name, index, count, onmove, onremove, onduplicate, disabled = false }: Props = $props();
	const label = $derived(name.trim() ? `“${name.trim()}”` : `entry ${index + 1}`);
</script>

<div class="flex shrink-0 items-center gap-0.5">
	<Button
		variant="ghost"
		size="icon"
		class="size-7 text-muted-foreground hover:text-foreground"
		disabled={disabled || index === 0}
		onclick={() => onmove(-1)}
		aria-label="Move {label} up"
		title="Move up"
	>
		<ChevronUp class="size-4" />
	</Button>
	<Button
		variant="ghost"
		size="icon"
		class="size-7 text-muted-foreground hover:text-foreground"
		disabled={disabled || index >= count - 1}
		onclick={() => onmove(1)}
		aria-label="Move {label} down"
		title="Move down"
	>
		<ChevronDown class="size-4" />
	</Button>
	{#if onduplicate}
		<Button
			variant="ghost"
			size="icon"
			class="size-7 text-muted-foreground hover:text-foreground"
			{disabled}
			onclick={onduplicate}
			aria-label="Duplicate {label}"
			title="Duplicate"
		>
			<Copy class="size-3.5" />
		</Button>
	{/if}
	<Button
		variant="ghost"
		size="icon"
		class="size-7 text-muted-foreground hover:text-danger-fg"
		{disabled}
		onclick={onremove}
		aria-label="Remove {label}"
		title="Remove"
	>
		<Trash2 class="size-3.5" />
	</Button>
</div>
