<script lang="ts" module>
	import type { Tone } from '$lib/constants/colors';

	export interface ChoiceOption {
		value: string;
		label: string;
		/** Native tooltip / longer explanation. */
		title?: string;
		/** Tint of the selected option (status meaning, never "good/bad score"). */
		tone?: Tone;
	}

	// Literal class strings so Tailwind generates them (tones of constants/colors.ts).
	const CHECKED: Record<Tone, string> = {
		success:
			'data-[state=checked]:border-success/50 data-[state=checked]:bg-success/10 data-[state=checked]:text-success-fg',
		warning:
			'data-[state=checked]:border-warning/50 data-[state=checked]:bg-warning/10 data-[state=checked]:text-warning-fg',
		danger:
			'data-[state=checked]:border-destructive/50 data-[state=checked]:bg-destructive/10 data-[state=checked]:text-danger-fg',
		info: 'data-[state=checked]:border-primary/60 data-[state=checked]:bg-primary/10 data-[state=checked]:text-primary',
		neutral:
			'data-[state=checked]:border-foreground/40 data-[state=checked]:bg-muted data-[state=checked]:text-foreground',
	};
</script>

<script lang="ts">
	/**
	 * ChoiceGroup — a one-of-N answer as a row of buttons (bits-ui RadioGroup:
	 * arrow keys move and select). Used for verdicts, outcomes and A/B choices.
	 *
	 * Props:
	 * - `value` (string; '' = unanswered), `options: ChoiceOption[]`.
	 * - `onchange(value)`: after the participant picks an option.
	 * - `readOnly`: shows the answer, cannot change it (the chosen option stays legible).
	 * - `aria-label` / `aria-labelledby` / `aria-describedby`: accessible name and help.
	 * - `size`: 'sm' | 'default'; `fullWidth`: equal-width options filling the row.
	 * - `invalid`: marks the group as still needing an answer.
	 */
	import { RadioGroup } from 'bits-ui';
	import { cn } from '$lib/utils';

	interface Props {
		value: string;
		options: ChoiceOption[];
		onchange?: (value: string) => void;
		readOnly?: boolean;
		size?: 'sm' | 'default';
		fullWidth?: boolean;
		invalid?: boolean;
		class?: string;
		'aria-label'?: string;
		'aria-labelledby'?: string;
		'aria-describedby'?: string;
	}

	let {
		value,
		options,
		onchange,
		readOnly = false,
		size = 'default',
		fullWidth = false,
		invalid = false,
		class: className,
		'aria-label': ariaLabel,
		'aria-labelledby': ariaLabelledby,
		'aria-describedby': ariaDescribedby,
	}: Props = $props();

	function pick(next: string) {
		if (readOnly || next === value) return;
		onchange?.(next);
	}
</script>

<RadioGroup.Root
	{value}
	onValueChange={pick}
	disabled={readOnly}
	orientation="horizontal"
	loop
	aria-label={ariaLabel}
	aria-labelledby={ariaLabelledby}
	aria-describedby={ariaDescribedby}
	class={cn('flex flex-wrap gap-1', fullWidth && 'w-full', className)}
>
	{#each options as option (option.value)}
		<RadioGroup.Item
			value={option.value}
			title={option.title}
			aria-label={option.title ? `${option.label}: ${option.title}` : undefined}
			class={cn(
				'inline-flex min-w-0 cursor-pointer items-center justify-center gap-1.5 rounded-md border border-border bg-surface-1 text-center font-medium text-muted-foreground transition-colors duration-150',
				'hover:border-primary/50 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40',
				'data-[state=checked]:font-semibold',
				CHECKED[option.tone ?? 'info'],
				'data-[disabled]:cursor-default data-[disabled]:hover:border-border data-[disabled]:data-[state=unchecked]:opacity-55',
				size === 'sm' ? 'h-7 px-2 text-xs' : 'min-h-9 px-3 py-1 text-sm',
				fullWidth && 'flex-1',
				invalid && value === '' && 'border-warning/60'
			)}
		>
			{option.label}
		</RadioGroup.Item>
	{/each}
</RadioGroup.Root>
