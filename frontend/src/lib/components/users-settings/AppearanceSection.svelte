<script lang="ts">
	import { getPreferences, VALID_COLORS } from '$lib/stores/preferences.svelte';
	import * as Card from '$lib/components/ui/card';
	import { Label } from '$lib/components/ui/label';
	import { Separator } from '$lib/components/ui/separator';
	import Sun from '@lucide/svelte/icons/sun';
	import Moon from '@lucide/svelte/icons/moon';
	import Monitor from '@lucide/svelte/icons/monitor';
	import Palette from '@lucide/svelte/icons/palette';

	const prefs = getPreferences();

	const avatarColorMap: Record<string, string> = {
		blue: 'bg-blue-500',
		indigo: 'bg-indigo-500',
		purple: 'bg-purple-500',
		pink: 'bg-pink-500',
		red: 'bg-red-500',
		orange: 'bg-orange-500',
		amber: 'bg-amber-500',
		green: 'bg-green-500',
		teal: 'bg-teal-500',
		cyan: 'bg-cyan-500',
	};

	const accentRingMap: Record<string, string> = {
		blue: 'ring-blue-500',
		indigo: 'ring-indigo-500',
		purple: 'ring-purple-500',
		pink: 'ring-pink-500',
		red: 'ring-red-500',
		orange: 'ring-orange-500',
		amber: 'ring-amber-500',
		green: 'ring-green-500',
		teal: 'ring-teal-500',
		cyan: 'ring-cyan-500',
	};
</script>

<Card.Root>
	<Card.Header>
		<Card.Title class="flex items-center gap-2">
			<Palette class="h-4 w-4 text-muted-foreground" />
			Appearance
		</Card.Title>
		<Card.Description>Theme, accent color, and avatar color for your account.</Card.Description>
	</Card.Header>
	<Card.Content class="space-y-6">
		<div class="space-y-2">
			<Label class="text-[11px] uppercase tracking-wider text-muted-foreground" style="font-family: var(--font-mono);">Theme</Label>
			<div class="flex flex-col gap-2 sm:flex-row sm:gap-3">
				{#each [
					{ value: 'light', label: 'Light', Icon: Sun },
					{ value: 'dark', label: 'Dark', Icon: Moon },
					{ value: 'system', label: 'System', Icon: Monitor },
				] as { value, label, Icon }}
					<button
						type="button"
						class="group flex flex-1 items-center gap-2 rounded-md border px-3 py-2.5 text-sm cursor-pointer transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring {prefs.theme ===
						value
							? 'border-primary bg-primary/5 text-foreground'
							: 'border-border text-muted-foreground hover:border-primary/40 hover:text-foreground'}"
						onclick={() => prefs.setTheme(value as 'light' | 'dark' | 'system')}
					>
						<Icon class="h-3.5 w-3.5" />
						<span class="font-medium">{label}</span>
						{#if prefs.theme === value}
							<span class="ml-auto text-[10px] text-primary" style="font-family: var(--font-mono);">active</span>
						{/if}
					</button>
				{/each}
			</div>
		</div>

		<Separator />

		<div class="space-y-2">
			<Label class="text-[11px] uppercase tracking-wider text-muted-foreground" style="font-family: var(--font-mono);">Accent color</Label>
			<p class="text-xs text-muted-foreground">Used for highlights, focus rings, and primary actions.</p>
			<div class="flex flex-wrap gap-2 pt-1">
				{#each VALID_COLORS as color}
					<button
						type="button"
						aria-label={`Accent color ${color}`}
						aria-pressed={prefs.accentColor === color}
						class="h-7 w-7 rounded-md cursor-pointer transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-offset-background {avatarColorMap[color]} {prefs.accentColor ===
						color
							? `ring-2 ring-offset-2 ring-offset-background ${accentRingMap[color]}`
							: 'opacity-80 hover:opacity-100 hover:scale-110'}"
						title={color}
						onclick={() => prefs.setAccentColor(color)}
					></button>
				{/each}
			</div>
		</div>

		<Separator />

		<div class="space-y-2">
			<Label class="text-[11px] uppercase tracking-wider text-muted-foreground" style="font-family: var(--font-mono);">Avatar color</Label>
			<p class="text-xs text-muted-foreground">Color of your initials avatar in the header and profile.</p>
			<div class="flex flex-wrap gap-2 pt-1">
				{#each VALID_COLORS as color}
					<button
						type="button"
						aria-label={`Avatar color ${color}`}
						aria-pressed={prefs.avatarColor === color}
						class="h-7 w-7 rounded-full cursor-pointer transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-offset-background {avatarColorMap[color]} {prefs.avatarColor ===
						color
							? `ring-2 ring-offset-2 ring-offset-background ${accentRingMap[color]}`
							: 'opacity-80 hover:opacity-100 hover:scale-110'}"
						title={color}
						onclick={() => prefs.setAvatarColor(color)}
					></button>
				{/each}
			</div>
		</div>
	</Card.Content>
</Card.Root>
