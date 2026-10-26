<script lang="ts">
	import { avatarColorClasses } from '$lib/constants/colors';
	import { getPreferences, VALID_COLORS, type ThemeMode } from '$lib/stores/preferences.svelte';
	import SettingsBlock from './SettingsBlock.svelte';
	import { cn } from '$lib/utils';
	import Check from '@lucide/svelte/icons/check';

	const prefs = getPreferences();

	// Each tile draws a miniature of the app in that theme (system = split). The
	// fixed hexes mirror app.css on purpose: tokens only describe the current theme.
	const THEMES: { value: ThemeMode; label: string; hint: string }[] = [
		{ value: 'light', label: 'Light', hint: 'Paper' },
		{ value: 'dark', label: 'Dark', hint: 'Observatory' },
		{ value: 'system', label: 'System', hint: 'Follows your OS' },
	];
</script>

{#snippet mini(dark: boolean)}
	<div class={cn('flex h-full w-full gap-1.5 p-2', dark ? 'bg-[#0b111d]' : 'bg-[#f5f5f1]')}>
		<div class={cn('w-5 rounded-sm', dark ? 'bg-[#080d17]' : 'bg-[#e8e8e1]')}></div>
		<div class="flex flex-1 flex-col gap-1.5">
			<div class={cn('h-2 w-2/3 rounded-sm', dark ? 'bg-[#e4e8f0]/70' : 'bg-[#141a26]/70')}></div>
			<div class={cn('flex-1 rounded-sm border', dark ? 'border-white/10 bg-[#111a2b]' : 'border-black/10 bg-white')}>
				<div class={cn('m-1.5 h-1.5 w-1/2 rounded-sm', dark ? 'bg-[#7b91ff]' : 'bg-[#2c44bd]')}></div>
			</div>
		</div>
	</div>
{/snippet}

<SettingsBlock title="Theme" description="Also switchable any time from the sun/moon button in the header.">
	<div class="settings-row">
		<div class="grid max-w-xl grid-cols-3 gap-3" role="radiogroup" aria-label="Theme">
			{#each THEMES as t (t.value)}
				{@const selected = prefs.theme === t.value}
				<button
					type="button"
					role="radio"
					aria-checked={selected}
					onclick={() => prefs.setTheme(t.value)}
					class={cn(
						'group cursor-pointer rounded-lg border p-1.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transition-none',
						selected ? 'border-primary ring-1 ring-primary' : 'border-border hover:border-foreground/30',
					)}
				>
					<div class="aspect-[16/10] overflow-hidden rounded-md border border-border" aria-hidden="true">
						{#if t.value === 'system'}
							<div class="grid h-full grid-cols-2">
								<div class="overflow-hidden">{@render mini(false)}</div>
								<div class="overflow-hidden">{@render mini(true)}</div>
							</div>
						{:else}
							{@render mini(t.value === 'dark')}
						{/if}
					</div>
					<div class="flex items-center justify-between gap-2 px-1 pt-2 pb-0.5">
						<span class="min-w-0">
							<span class="block text-sm font-medium">{t.label}</span>
							<span class="block truncate text-xs text-muted-foreground">{t.hint}</span>
						</span>
						<span
							class={cn(
								'flex size-5 shrink-0 items-center justify-center rounded-full border',
								selected ? 'border-primary bg-primary text-primary-foreground' : 'border-border',
							)}
							aria-hidden="true"
						>
							{#if selected}<Check class="size-3" />{/if}
						</span>
					</div>
				</button>
			{/each}
		</div>
	</div>
</SettingsBlock>

<SettingsBlock title="Avatar color" description="Background of your initials in the header and on your profile.">
	<div class="settings-row">
		<div class="flex flex-wrap gap-3" role="radiogroup" aria-label="Avatar color">
			{#each VALID_COLORS as color (color)}
				{@const selected = prefs.avatarColor === color}
				<button
					type="button"
					role="radio"
					aria-checked={selected}
					aria-label={color}
					title={color}
					class={cn(
						'flex size-9 cursor-pointer items-center justify-center rounded-full text-white transition-transform focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-card motion-reduce:transition-none',
						avatarColorClasses[color],
						selected ? 'ring-2 ring-foreground ring-offset-2 ring-offset-card' : 'hover:scale-110',
					)}
					onclick={() => prefs.setAvatarColor(color)}
				>
					{#if selected}<Check class="size-4" aria-hidden="true" />{/if}
				</button>
			{/each}
		</div>
	</div>
</SettingsBlock>
