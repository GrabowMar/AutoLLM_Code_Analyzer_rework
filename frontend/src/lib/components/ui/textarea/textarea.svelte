<script lang="ts">
import { cn } from '$lib/utils';

let {
class: className = '',
value = $bindable(''),
placeholder = '',
disabled = false,
rows = 4,
...rest
}: {
class?: string;
value?: string;
placeholder?: string;
disabled?: boolean;
rows?: number;
[key: string]: unknown;
} = $props();
</script>

<textarea
{rows}
{placeholder}
{disabled}
bind:value
class={cn(
'flex min-h-[80px] w-full rounded-md border border-input bg-surface-1 px-3 py-2 text-sm placeholder:text-muted-foreground/70 hover:border-primary/40 transition-colors focus-visible:outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30 disabled:cursor-not-allowed disabled:opacity-50',
className
)}
{...rest}
></textarea>
