<script lang="ts">
	import { cn } from '$lib/utils';
	import { toneClasses, statusTone, liveStatuses, type Tone } from '$lib/constants/colors';

	interface Props {
		status: string | null | undefined;
		/** Force a tone instead of deriving it from the status. */
		tone?: Tone;
		/** Per-domain status→tone overrides, e.g. `containerTones`. */
		tones?: Partial<Record<string, Tone>>;
		/** Display text; defaults to the humanized status. */
		label?: string;
		class?: string;
	}

	let { status, tone, tones, label, class: className }: Props = $props();

	const resolved = $derived(tone ?? statusTone(status, tones));
	const live = $derived(liveStatuses.has((status ?? '').toLowerCase()));
	const text = $derived(label ?? (status ?? 'unknown').replace(/_/g, ' '));
</script>

<span
	class={cn(
		'inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-medium capitalize leading-4',
		toneClasses[resolved],
		className
	)}
>
	<span class={cn('status-dot h-1.5 w-1.5', live && 'status-dot-live')} aria-hidden="true"></span>
	{text}
</span>
