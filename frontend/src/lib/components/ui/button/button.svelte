<script lang="ts">
	import type { HTMLButtonAttributes, HTMLAnchorAttributes } from 'svelte/elements';
	import { cn } from '$lib/utils';

	type ButtonVariant = 'default' | 'destructive' | 'outline' | 'secondary' | 'ghost' | 'link';
	type ButtonSize = 'default' | 'sm' | 'lg' | 'icon';

	interface ButtonProps {
		class?: string;
		variant?: ButtonVariant;
		size?: ButtonSize;
		href?: string;
		disabled?: boolean;
		type?: 'button' | 'submit' | 'reset';
		children?: import('svelte').Snippet;
		onclick?: (e: MouseEvent) => void;
		[key: string]: unknown;
	}

	let { class: className, variant = 'default', size = 'default', href, children, ...restProps }: ButtonProps = $props();

	const variantClasses: Record<ButtonVariant, string> = {
		default: 'bg-primary text-primary-foreground shadow-sm hover:bg-primary/90 hover:shadow-md hover:-translate-y-px active:translate-y-0',
		destructive: 'bg-destructive text-white shadow-sm hover:bg-destructive/90 hover:shadow-md',
		outline: 'border border-border bg-transparent hover:bg-accent hover:text-accent-foreground hover:border-primary/50',
		secondary: 'bg-secondary text-secondary-foreground shadow-sm hover:bg-secondary/80',
		ghost: 'hover:bg-accent hover:text-accent-foreground',
		link: 'text-primary underline-offset-4 hover:underline decoration-primary/60',
	};

	const sizeClasses: Record<ButtonSize, string> = {
		default: 'h-9 px-4 py-2',
		sm: 'h-8 rounded px-3 text-xs',
		lg: 'h-10 rounded-md px-6',
		icon: 'h-9 w-9',
	};

	const baseClasses = 'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium tracking-tight transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg]:size-4 cursor-pointer touch-manipulation motion-reduce:transition-none motion-reduce:hover:transform-none';
</script>

{#if href}
	<a
		{href}
		class={cn(baseClasses, variantClasses[variant], sizeClasses[size], className)}
		{...restProps}
	>
		{#if children}{@render children()}{/if}
	</a>
{:else}
	<button
		class={cn(baseClasses, variantClasses[variant], sizeClasses[size], className)}
		{...restProps}
	>
		{#if children}{@render children()}{/if}
	</button>
{/if}
