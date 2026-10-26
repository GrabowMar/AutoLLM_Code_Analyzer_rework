<script lang="ts">
	/**
	 * ScopeEditor — which files and lines a profile measures.
	 *
	 * Props: `config` (`ScopeConfig`, replaced never mutated), `onchange(scope)`,
	 * `errors` (keys relative to the scope: `measured_origins`, ...),
	 * `readonly`, `idPrefix` (default 'scope').
	 *
	 * Findings are counted and SLOC measured over the same files and lines,
	 * so the numerator and denominator of every density share this scope.
	 */
	import {
		FILE_KINDS,
		FILE_ORIGINS,
		type FileKind,
		type FileOrigin,
		type ScopeConfig
	} from '$lib/api/analysis';
	import { cn } from '$lib/utils';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Switch } from '$lib/components/ui/switch';
	import ChoiceChecklist from './ChoiceChecklist.svelte';
	import NumberField from './NumberField.svelte';
	import { KIND_INFO, ORIGIN_INFO } from './profile-utils';
	import Plus from '@lucide/svelte/icons/plus';
	import X from '@lucide/svelte/icons/x';

	interface Props {
		config: ScopeConfig;
		onchange?: (config: ScopeConfig) => void;
		errors?: Record<string, string>;
		readonly?: boolean;
		idPrefix?: string;
	}

	let { config, onchange, errors = {}, readonly = false, idPrefix = 'scope' }: Props = $props();

	const MAX_FILE_BYTES = { min: 1024, max: 16 * 1024 * 1024 };

	function set<K extends keyof ScopeConfig>(key: K, value: ScopeConfig[K]) {
		onchange?.({ ...config, [key]: value });
	}

	const id = (name: string) => `${idPrefix}-${name}`;

	const originItems = FILE_ORIGINS.map((o) => ({
		value: o,
		label: ORIGIN_INFO[o].label,
		description: ORIGIN_INFO[o].description
	}));

	const measuredKindItems = $derived(
		FILE_KINDS.map((k) => {
			const excluded = config.exclude_kinds.includes(k);
			return {
				value: k,
				label: KIND_INFO[k].label,
				description: excluded ? 'Excluded from the sample below' : KIND_INFO[k].description,
				disabled: excluded && !config.measured_kinds.includes(k),
				title: excluded
					? 'Excluded files never reach the tools, so they cannot be measured'
					: undefined
			};
		})
	);

	const excludeKindItems = FILE_KINDS.map((k) => ({
		value: k,
		label: KIND_INFO[k].label,
		description: k === 'source' ? 'Never excluded by kind' : KIND_INFO[k].description,
		disabled: k === 'source',
		title: k === 'source' ? 'Source files cannot be excluded by kind' : undefined
	}));

	// ── exclude globs ──────────────────────────────────────────────────
	let globDraft = $state('');
	const globDraftTrimmed = $derived(globDraft.trim());
	const globDuplicate = $derived(
		!!globDraftTrimmed && config.exclude_globs.includes(globDraftTrimmed)
	);

	function addGlob() {
		if (!globDraftTrimmed || globDuplicate) return;
		set('exclude_globs', [...config.exclude_globs, globDraftTrimmed]);
		globDraft = '';
	}

	function removeGlob(glob: string) {
		set(
			'exclude_globs',
			config.exclude_globs.filter((g) => g !== glob)
		);
	}

	function formatBytes(n: number): string {
		if (n >= 1024 * 1024)
			return `${(n / (1024 * 1024)).toLocaleString(undefined, { maximumFractionDigits: 2 })} MiB`;
		if (n >= 1024)
			return `${(n / 1024).toLocaleString(undefined, { maximumFractionDigits: 1 })} KiB`;
		return `${n} B`;
	}
</script>

<div class="space-y-6" data-slot="scope-editor">
	<p class="text-xs leading-relaxed text-muted-foreground">
		Every density uses the same scope for its numerator and denominator: findings are counted and
		SLOC measured over the same files and lines. By default that is the code the model wrote.
	</p>
	{#if errors['']}
		<p
			class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-danger-fg"
			role="alert"
		>
			{errors['']}
		</p>
	{/if}

	<section class="space-y-2" aria-labelledby={id('origins-label')}>
		<h4 id={id('origins-label')} class="text-sm font-medium">Measured origins</h4>
		<p class="text-[11px] leading-4 text-muted-foreground">
			Whose code is measured. Other files are still scanned, but their findings are not counted.
		</p>
		<ChoiceChecklist
			id={id('origins')}
			labelledBy={id('origins-label')}
			items={originItems}
			value={config.measured_origins}
			disabled={readonly}
			error={errors['measured_origins']}
			columns="grid-cols-1 sm:grid-cols-2 lg:grid-cols-3"
			onchange={(values) => set('measured_origins', values as FileOrigin[])}
		/>
	</section>

	<section class="space-y-2" aria-labelledby={id('kinds-label')}>
		<h4 id={id('kinds-label')} class="text-sm font-medium">Measured file kinds</h4>
		<p class="text-[11px] leading-4 text-muted-foreground">
			Source only by default; add config or env to count model-written configuration.
		</p>
		<ChoiceChecklist
			id={id('kinds')}
			labelledBy={id('kinds-label')}
			items={measuredKindItems}
			value={config.measured_kinds}
			disabled={readonly}
			error={errors['measured_kinds']}
			columns="grid-cols-1 sm:grid-cols-2 lg:grid-cols-3"
			onchange={(values) => set('measured_kinds', values as FileKind[])}
		/>
	</section>

	<section class="flex items-start gap-3">
		<Switch
			id={id('line-attribution')}
			checked={config.line_attribution}
			label="Line attribution"
			describedBy={id('line-attribution-help')}
			disabled={readonly}
			onCheckedChange={(on) => set('line_attribution', on)}
		/>
		<div class="min-w-0">
			<Label for={id('line-attribution')}>Line attribution</Label>
			<p id={id('line-attribution-help')} class="mt-1 text-[11px] leading-4 text-muted-foreground">
				For scaffold files the agent modified, count only the lines it changed (diffed against the
				stack scaffold).
			</p>
			{#if errors['line_attribution']}<p class="text-[11px] text-danger-fg">
					{errors['line_attribution']}
				</p>{/if}
		</div>
	</section>

	<div class="border-t border-border pt-5">
		<h4 class="section-label">Excluded from the sample</h4>
		<p class="mt-1 text-[11px] leading-4 text-muted-foreground">
			Excluded files never reach the tools or the reviewer; the manifest records why.
		</p>
	</div>

	<section class="space-y-2" aria-labelledby={id('exclude-kinds-label')}>
		<h4 id={id('exclude-kinds-label')} class="text-sm font-medium">Excluded kinds</h4>
		<ChoiceChecklist
			id={id('exclude-kinds')}
			labelledBy={id('exclude-kinds-label')}
			items={excludeKindItems}
			value={config.exclude_kinds}
			disabled={readonly}
			error={errors['exclude_kinds']}
			columns="grid-cols-1 sm:grid-cols-2 lg:grid-cols-3"
			onchange={(values) => set('exclude_kinds', values as FileKind[])}
		/>
	</section>

	<section class="space-y-2" aria-labelledby={id('globs-label')}>
		<h4 id={id('globs-label')} class="text-sm font-medium">Excluded paths</h4>
		<p class="text-[11px] leading-4 text-muted-foreground">
			Glob patterns relative to the app root, e.g. <code class="font-mono">**/dist/**</code>.
		</p>
		{#if config.exclude_globs.length > 0}
			<ul class="flex flex-wrap gap-1.5" aria-labelledby={id('globs-label')}>
				{#each config.exclude_globs as glob (glob)}
					<li
						class="inline-flex max-w-full items-center gap-1 rounded-full border border-border bg-muted/40 py-0.5 pl-2 pr-1 font-mono text-xs"
					>
						<span class="truncate">{glob}</span>
						{#if !readonly}
							<button
								type="button"
								class="flex size-4 shrink-0 cursor-pointer items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
								aria-label="Remove {glob}"
								onclick={() => removeGlob(glob)}
							>
								<X class="size-3" />
							</button>
						{/if}
					</li>
				{/each}
			</ul>
		{:else}
			<p class="text-xs text-muted-foreground">No path exclusions.</p>
		{/if}
		{#if !readonly}
			<form
				class="flex max-w-md gap-2"
				onsubmit={(e) => {
					e.preventDefault();
					addGlob();
				}}
			>
				<Input
					id={id('glob-input')}
					bind:value={globDraft}
					placeholder="**/generated/**"
					autocomplete="off"
					spellcheck={false}
					aria-label="Glob to exclude"
					aria-describedby={globDuplicate ? id('glob-dup') : undefined}
					class={cn('h-8 font-mono text-xs', globDuplicate && 'border-warning')}
				/>
				<Button
					type="submit"
					variant="outline"
					size="sm"
					disabled={!globDraftTrimmed || globDuplicate}
				>
					<Plus />Add
				</Button>
			</form>
			{#if globDuplicate}<p id={id('glob-dup')} class="text-[11px] text-warning-fg">
					Already excluded.
				</p>{/if}
		{/if}
		{#if errors['exclude_globs']}<p class="text-[11px] text-danger-fg">
				{errors['exclude_globs']}
			</p>{/if}
	</section>

	<section class="max-w-xs space-y-1.5">
		<Label for={id('max-bytes')} class="text-sm">Largest file scanned</Label>
		<NumberField
			id={id('max-bytes')}
			value={config.max_file_bytes}
			integer
			step={1024}
			min={MAX_FILE_BYTES.min}
			max={MAX_FILE_BYTES.max}
			unit="bytes"
			error={errors['max_file_bytes']}
			disabled={readonly}
			describedBy={id('max-bytes-help')}
			onchange={(n) => n !== null && set('max_file_bytes', n)}
		/>
		<p id={id('max-bytes-help')} class="text-[11px] leading-4 text-muted-foreground">
			{formatBytes(config.max_file_bytes)} · larger files are left out of the sample.
		</p>
	</section>
</div>
