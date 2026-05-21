<script lang="ts">
	import type { HTMLAttributes } from 'svelte/elements';
	import { cn } from '$lib/utils';

	type BadgeVariant = 'default' | 'secondary' | 'destructive' | 'outline';

	let { class: className, variant = 'default', children, ...restProps }: HTMLAttributes<HTMLDivElement> & { variant?: BadgeVariant; children?: import('svelte').Snippet } = $props();

	const variantClasses: Record<BadgeVariant, string> = {
		default: 'border-transparent bg-[color:var(--primary)]/15 text-[color:var(--primary)]',
		secondary: 'border-border bg-muted text-muted-foreground',
		destructive: 'border-transparent bg-[color:var(--destructive)]/15 text-[color:var(--destructive)]',
		outline: 'border-border text-foreground',
	};
</script>

<div
	class={cn(
		'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium tabular-nums transition-colors',
		variantClasses[variant],
		className
	)}
	{...restProps}
>
	{#if children}{@render children()}{/if}
</div>
