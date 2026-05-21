<script lang="ts">
	import { cn } from '$lib/utils';

	interface Props {
		checked: boolean;
		disabled?: boolean;
		id?: string;
		label?: string;
		describedBy?: string;
		size?: 'sm' | 'default';
		class?: string;
		onCheckedChange?: (checked: boolean) => void;
	}

	let {
		checked = $bindable(),
		disabled = false,
		id,
		label,
		describedBy,
		size = 'default',
		class: className,
		onCheckedChange,
	}: Props = $props();

	const sizes = {
		sm: { track: 'h-4 w-7', thumb: 'h-3 w-3', translate: 'translate-x-3' },
		default: { track: 'h-5 w-9', thumb: 'h-4 w-4', translate: 'translate-x-4' },
	} as const;

	function handleClick() {
		if (disabled) return;
		const next = !checked;
		checked = next;
		onCheckedChange?.(next);
	}

	function handleKeydown(e: KeyboardEvent) {
		if (disabled) return;
		if (e.key === ' ' || e.key === 'Enter') {
			e.preventDefault();
			handleClick();
		}
	}
</script>

<button
	type="button"
	role="switch"
	{id}
	aria-checked={checked}
	aria-label={label}
	aria-describedby={describedBy}
	aria-disabled={disabled}
	{disabled}
	onclick={handleClick}
	onkeydown={handleKeydown}
	class={cn(
		'relative inline-flex shrink-0 items-center rounded-full transition-colors duration-200 ease-out',
		'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background',
		'motion-reduce:transition-none',
		disabled ? 'cursor-not-allowed opacity-60' : 'cursor-pointer',
		sizes[size].track,
		checked ? 'bg-primary' : 'bg-muted',
		className,
	)}
>
	<span
		class={cn(
			'pointer-events-none inline-block translate-x-0.5 transform rounded-full bg-white shadow ring-0 transition-transform duration-200 ease-out motion-reduce:transition-none',
			sizes[size].thumb,
			checked && sizes[size].translate,
		)}
	></span>
</button>
