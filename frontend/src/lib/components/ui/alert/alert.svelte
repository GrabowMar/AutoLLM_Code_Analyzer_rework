<script lang="ts">
	import type { HTMLAttributes } from 'svelte/elements';
	import { cn } from '$lib/utils';

	type AlertVariant = 'default' | 'destructive';

	let { class: className, variant = 'default', children, ...restProps }: HTMLAttributes<HTMLDivElement> & { variant?: AlertVariant; children?: import('svelte').Snippet } = $props();

	const variantClasses: Record<AlertVariant, string> = {
		default: 'bg-card text-foreground border-border',
		destructive: 'border-l-4 border-l-destructive border-y border-r border-y-border border-r-border bg-[color:var(--destructive)]/5 text-foreground [&>svg]:text-destructive',
	};
</script>

<div
	role="alert"
	class={cn(
		'relative w-full rounded-md border px-4 py-3 text-sm [&>svg+div]:translate-y-[-3px] [&>svg]:absolute [&>svg]:left-4 [&>svg]:top-3.5 [&>svg]:size-4 [&>svg~*]:pl-7',
		variantClasses[variant],
		className
	)}
	{...restProps}
>
	{#if children}{@render children()}{/if}
</div>
