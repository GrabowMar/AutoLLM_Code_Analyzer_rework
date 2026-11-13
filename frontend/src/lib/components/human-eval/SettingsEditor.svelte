<script lang="ts">
	/**
	 * SettingsEditor — task settings: blinding, allocation, pairing (pairwise
	 * tasks), access, hosting (incl. app-state isolation and reset) and quality.
	 *
	 *   <SettingsEditor mode={task.mode} bind:settings frozen={!!task.frozen_at} baselineOptions={conditions} />
	 *
	 * Props:
	 * - `mode`: pairing settings only show for pairwise tasks.
	 * - `settings` (bindable): edited in place.
	 * - `frozen`: the design is frozen (task opened once) — only the keys that do
	 *   not change what is measured stay editable (allocation limits / timeout /
	 *   skip / overflow, access, hosting on-demand / idle stop / max running, quality).
	 * - `lockQuality`: with `frozen`, quality is frozen too — the frozen analysis plan
	 *   excludes speeders, so the threshold decides which data count (backend
	 *   `editable_after_freeze(design)`). Pass `speedingExcluded(task.design)`.
	 * - `readOnly`: nothing editable.
	 * - `baselineOptions`: experiment conditions for the vs-baseline pairing.
	 * - `errors` (optional): `{path, message}[]`; settings paths with or without a `settings.` prefix.
	 */
	import * as Card from '$lib/components/ui/card';
	import * as Select from '$lib/components/ui/select';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Label } from '$lib/components/ui/label';
	import { Segmented } from '$lib/components/ui/segmented';
	import NumberField from '$lib/components/studies/shared/NumberField.svelte';
	import Lock from '@lucide/svelte/icons/lock';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import type {
		AllocationStrategy,
		HostingIsolation,
		PairingStrategy,
		TaskMode,
		TaskSettings,
	} from '$lib/api/types/human-eval';
	import { areaErrors, messageAt, type PathError } from './design/errors';
	import { editableSettingKeys, settingLocked } from './freeze';

	interface Props {
		mode: TaskMode;
		settings: TaskSettings;
		frozen?: boolean;
		lockQuality?: boolean;
		readOnly?: boolean;
		baselineOptions?: { id: number; label: string }[];
		errors?: PathError[];
	}

	let {
		mode,
		settings = $bindable(),
		frozen = false,
		lockQuality = false,
		readOnly = false,
		baselineOptions = [],
		errors = [],
	}: Props = $props();

	/** Keys that stay editable after the design is frozen (spec §5.1 / R1; quality only without a speeding exclusion). */
	const editable = $derived(editableSettingKeys(lockQuality));

	function locked(key: string): boolean {
		return settingLocked(key, { frozen, readOnly, editable });
	}
	const qualityFrozen = $derived(frozen && !readOnly && lockQuality);

	const errs = $derived(areaErrors(errors, 'settings'));
	function err(group: string, field: string): string {
		return messageAt(errs, group, field);
	}

	const resetBlocked = $derived(settings.hosting.isolation !== 'exclusive');
	const accessCount = $derived(
		[settings.access.allow_invite_links, settings.access.allow_access_codes, settings.access.allow_accounts].filter(Boolean).length
	);
	const baselineItems = $derived(baselineOptions.map((o) => ({ value: String(o.id), label: o.label })));
	const unknownBaseline = $derived(
		settings.pairing.baseline_condition_id != null && !baselineOptions.some((o) => o.id === settings.pairing.baseline_condition_id)
	);
	/** Errors shown next to a rendered field; everything else goes to the summary above the cards. */
	function placed(path: string[]): boolean {
		if (path.length !== 2) return false;
		const key = `${path[0]}.${path[1]}`;
		switch (key) {
			case 'allocation.strategy':
			case 'allocation.max_items_per_participant':
			case 'allocation.assignment_timeout_minutes':
			case 'hosting.isolation':
			case 'hosting.reset':
			case 'hosting.idle_stop_minutes':
			case 'hosting.max_running_apps':
			case 'quality.min_seconds_per_item':
				return true;
			case 'allocation.ratings_per_item':
				return settings.allocation.strategy === 'balanced';
			case 'pairing.strategy':
				return mode === 'pairwise';
			case 'pairing.k':
				return mode === 'pairwise' && settings.pairing.strategy === 'random_k';
			case 'pairing.baseline_condition_id':
				return mode === 'pairwise' && settings.pairing.strategy === 'vs_baseline';
			default:
				return false;
		}
	}
	const unplaced = $derived(
		errs.filter((e) => !placed(e.path)).map((e) => (e.path.length ? `${e.path.join(' › ').replace(/_/g, ' ')}: ${e.message}` : e.message))
	);

	function setIsolation(value: string) {
		const isolation: HostingIsolation = value === 'exclusive' ? 'exclusive' : 'shared';
		settings.hosting.isolation = isolation;
		// Resetting per assignment needs exclusive use of the app.
		if (isolation === 'shared' && settings.hosting.reset === 'per_assignment') settings.hosting.reset = 'none';
	}

	function setReset(value: string) {
		if (value === 'per_assignment' && resetBlocked) return;
		settings.hosting.reset = value === 'per_assignment' ? 'per_assignment' : 'none';
	}

	function setBaseline(value: string) {
		settings.pairing.baseline_condition_id = value ? Number(value) : null;
	}

	/** Setter for required numeric fields: ignore the empty value NumberField reports while typing. */
	function keep(apply: (v: number) => void) {
		return (v: number | null) => {
			if (v != null) apply(v);
		};
	}
</script>

{#snippet frozenMark(show: boolean)}
	{#if show}
		<span class="inline-flex items-center gap-1 text-[11px] font-normal text-muted-foreground" title="Frozen when the task first opened">
			<Lock class="size-3" aria-hidden="true" />Frozen
		</span>
	{/if}
{/snippet}

{#snippet note(text: string, error = '')}
	<p class="text-[11px] {error ? 'text-danger-fg' : 'text-muted-foreground'}">{error || text}</p>
{/snippet}

<div class="space-y-4">
	{#if unplaced.length}
		<div class="rounded-md border border-destructive/40 bg-destructive/5 px-4 py-2.5 text-xs text-danger-fg" role="alert">
			{#each unplaced as message (message)}<p>{message}</p>{/each}
		</div>
	{/if}

	<div class="grid gap-4 lg:grid-cols-2">
		<!-- Allocation -->
		<Card.Root class="gap-3">
			<Card.Header>
				<Card.Title class="flex items-center gap-2">Allocation {@render frozenMark(frozen && !readOnly)}</Card.Title>
				<Card.Description>Who rates which {mode === 'pairwise' ? 'pair' : 'app'}, and how often.</Card.Description>
			</Card.Header>
			<Card.Content class="space-y-4">
				<div class="space-y-1.5">
					<p class="section-label" id="settings-strategy-label">Strategy</p>
					<Segmented
						size="sm"
						aria-labelledby="settings-strategy-label"
						value={settings.allocation.strategy}
						disabled={locked('allocation.strategy')}
						onValueChange={(v) => (settings.allocation.strategy = v as AllocationStrategy)}
						items={[
							{ value: 'balanced', label: 'Balanced' },
							{ value: 'all', label: 'Every item' },
						]}
					/>
					{@render note(
						settings.allocation.strategy === 'balanced'
							? 'Each participant gets the item with the fewest ratings next, until every item reaches the target.'
							: 'Every participant rates every item, in a seeded order interleaved across conditions.',
						err('allocation', 'strategy')
					)}
				</div>
				{#if settings.allocation.strategy === 'balanced'}
					<div class="grid gap-3 sm:grid-cols-2">
						<NumberField
							id="settings-rpi"
							label="Ratings per item"
							bind:value={() => settings.allocation.ratings_per_item, keep((v) => (settings.allocation.ratings_per_item = v))}
							min={1}
							max={50}
							required
							disabled={locked('allocation.ratings_per_item')}
							error={err('allocation', 'ratings_per_item')}
						/>
						<label class="flex items-start gap-2.5 pt-5 text-sm">
							<Checkbox class="mt-0.5" bind:checked={settings.allocation.allow_overflow} disabled={locked('allocation.allow_overflow')} />
							<span>
								Allow overflow
								<span class="block text-xs text-muted-foreground">Keep handing out items once all reached the target.</span>
							</span>
						</label>
					</div>
				{/if}
				<div class="grid gap-3 sm:grid-cols-2">
					<NumberField
						id="settings-max-items"
						label="Items per participant"
						bind:value={settings.allocation.max_items_per_participant}
						min={1}
						max={10000}
						placeholder="Unlimited"
						disabled={locked('allocation.max_items_per_participant')}
						hint="Empty = no limit."
						error={err('allocation', 'max_items_per_participant')}
					/>
					<NumberField
						id="settings-timeout"
						label="Assignment timeout (minutes)"
						bind:value={() => settings.allocation.assignment_timeout_minutes, keep((v) => (settings.allocation.assignment_timeout_minutes = v))}
						min={5}
						max={1440}
						required
						disabled={locked('allocation.assignment_timeout_minutes')}
						hint="Idle assignments expire (under “Every item” only when apps are used one participant at a time)."
						error={err('allocation', 'assignment_timeout_minutes')}
					/>
				</div>
				<label class="flex items-start gap-2.5 text-sm">
					<Checkbox class="mt-0.5" bind:checked={settings.allocation.allow_skip} disabled={locked('allocation.allow_skip')} />
					<span>
						Participants may skip an item
						<span class="block text-xs text-muted-foreground">With a reason: app broken, platform unavailable, cannot evaluate, conflict of interest, other.</span>
					</span>
				</label>
			</Card.Content>
		</Card.Root>

		<!-- Blinding -->
		<Card.Root class="gap-3">
			<Card.Header>
				<Card.Title class="flex items-center gap-2">What participants see {@render frozenMark(frozen && !readOnly)}</Card.Title>
				<Card.Description>Model, condition, experiment and run names are never shown; apps appear as “App 07”.</Card.Description>
			</Card.Header>
			<Card.Content class="space-y-3">
				<label class="flex items-start gap-2.5 text-sm">
					<Checkbox class="mt-0.5" bind:checked={settings.blinding.show_spec} disabled={locked('blinding.show_spec')} />
					<span>
						App specification
						<span class="block text-xs text-muted-foreground">The requirements and endpoints the app was generated from.</span>
					</span>
				</label>
				<label class="flex items-start gap-2.5 text-sm">
					<Checkbox class="mt-0.5" bind:checked={settings.blinding.show_source_code} disabled={locked('blinding.show_source_code')} />
					<span>
						Source code
						<span class="block text-xs text-muted-foreground">A read-only file viewer. Code can reveal the model (comments, layout); the task warns before opening when it finds model names.</span>
					</span>
				</label>
				<label class="flex items-start gap-2.5 text-sm">
					<Checkbox class="mt-0.5" bind:checked={settings.blinding.show_admin_credentials} disabled={locked('blinding.show_admin_credentials')} />
					<span>
						Admin login
						<span class="block text-xs text-muted-foreground">The app's built-in admin account, for testing admin features.</span>
					</span>
				</label>
			</Card.Content>
		</Card.Root>

		{#if mode === 'pairwise'}
			<!-- Pairing -->
			<Card.Root class="gap-3">
				<Card.Header>
					<Card.Title class="flex items-center gap-2">Pairing {@render frozenMark(frozen && !readOnly)}</Card.Title>
					<Card.Description>Pairs are only formed between apps built from the same spec.</Card.Description>
				</Card.Header>
				<Card.Content class="space-y-4">
					<div class="space-y-1.5">
						<p class="section-label" id="settings-pairing-label">Pairs</p>
						<Segmented
							size="sm"
							aria-labelledby="settings-pairing-label"
							value={settings.pairing.strategy}
							disabled={locked('pairing.strategy')}
							onValueChange={(v) => (settings.pairing.strategy = v as PairingStrategy)}
							items={[
								{ value: 'all_pairs', label: 'All pairs' },
								{ value: 'random_k', label: 'Random k per app' },
								{ value: 'vs_baseline', label: 'Against baseline' },
							]}
						/>
						{@render note(
							settings.pairing.strategy === 'all_pairs'
								? 'Every unordered pair within a spec group.'
								: settings.pairing.strategy === 'random_k'
									? 'A seeded random set of pairs until every app is in at least k pairs.'
									: 'Each app against the baseline condition’s app(s) of the same spec, preferring the same repeat.',
							err('pairing', 'strategy')
						)}
					</div>
					{#if settings.pairing.strategy === 'random_k'}
						<div class="sm:w-48">
							<NumberField
								id="settings-k"
								label="Pairs per app (k)"
								bind:value={() => settings.pairing.k, keep((v) => (settings.pairing.k = v))}
								min={1}
								max={20}
								required
								disabled={locked('pairing.k')}
								error={err('pairing', 'k')}
							/>
						</div>
					{/if}
					{#if settings.pairing.strategy === 'vs_baseline'}
						<div class="space-y-1.5 sm:w-80">
							<Label class="text-xs" for="settings-baseline">Baseline condition</Label>
							<Select.Simple
								id="settings-baseline"
								value={settings.pairing.baseline_condition_id != null ? String(settings.pairing.baseline_condition_id) : ''}
								placeholder={baselineItems.length ? 'The experiment’s baseline' : 'Link an experiment to choose'}
								clearLabel="The experiment’s baseline"
								items={baselineItems}
								disabled={locked('pairing.baseline_condition_id') || baselineItems.length === 0}
								onValueChange={setBaseline}
							/>
							{@render note(
								unknownBaseline
									? `Condition #${settings.pairing.baseline_condition_id} is not in the linked experiment.`
									: 'Leave empty to use the linked experiment’s pre-registered baseline.',
								err('pairing', 'baseline_condition_id')
							)}
						</div>
					{/if}
					<label class="flex items-start gap-2.5 text-sm">
						<Checkbox class="mt-0.5" bind:checked={settings.pairing.cross_condition_only} disabled={locked('pairing.cross_condition_only')} />
						<span>
							Only pair different conditions
							<span class="block text-xs text-muted-foreground">Skip pairs of two apps from the same condition (same model without an experiment).</span>
						</span>
					</label>
				</Card.Content>
			</Card.Root>
		{/if}

		<!-- Hosting -->
		<Card.Root class="gap-3">
			<Card.Header>
				<Card.Title>Hosting</Card.Title>
				<Card.Description>How participants reach the running apps.</Card.Description>
			</Card.Header>
			<Card.Content class="space-y-4">
				<div class="space-y-1.5">
					<p class="section-label flex items-center gap-2" id="settings-isolation-label">App use {@render frozenMark(frozen && !readOnly)}</p>
					<Segmented
						size="sm"
						aria-labelledby="settings-isolation-label"
						value={settings.hosting.isolation}
						disabled={locked('hosting.isolation')}
						onValueChange={setIsolation}
						items={[
							{ value: 'shared', label: 'Shared' },
							{ value: 'exclusive', label: 'One participant at a time' },
						]}
					/>
					{@render note(
						settings.hosting.isolation === 'exclusive'
							? 'An app is in at most one open assignment at a time.'
							: 'Several participants may use the same app at once.',
						err('hosting', 'isolation')
					)}
				</div>
				<div class="space-y-1.5">
					<p class="section-label flex items-center gap-2" id="settings-reset-label">App state {@render frozenMark(frozen && !readOnly)}</p>
					<Segmented
						size="sm"
						aria-labelledby="settings-reset-label"
						value={settings.hosting.reset}
						disabled={locked('hosting.reset')}
						onValueChange={setReset}
						items={[
							{ value: 'none', label: 'Keep' },
							{
								value: 'per_assignment',
								label: 'Reset per assignment',
								disabled: resetBlocked,
								title: resetBlocked ? 'Needs “One participant at a time”' : 'Recreate the container from its image before each new assignment',
							},
						]}
					/>
					{#if err('hosting', 'reset')}
						{@render note('', err('hosting', 'reset'))}
					{:else if settings.hosting.reset === 'per_assignment'}
						{@render note('The app container is recreated from its image before each new assignment, so nobody sees earlier participants’ data.')}
					{:else}
						<p class="flex items-start gap-1.5 text-[11px] text-warning-fg">
							<TriangleAlert class="mt-px size-3 shrink-0" aria-hidden="true" />
							Participants may see accounts and data earlier participants created (shared app state).{resetBlocked ? ' Resetting needs one participant at a time.' : ''}
						</p>
					{/if}
				</div>
				<label class="flex items-start gap-2.5 text-sm">
					<Checkbox class="mt-0.5" bind:checked={settings.hosting.on_demand} disabled={locked('hosting.on_demand')} />
					<span>
						Start apps on demand
						<span class="block text-xs text-muted-foreground">A participant opening a stopped app starts it; otherwise only apps you start are available.</span>
					</span>
				</label>
				<div class="grid gap-3 sm:grid-cols-2">
					<NumberField
						id="settings-idle"
						label="Stop idle apps after (minutes)"
						bind:value={() => settings.hosting.idle_stop_minutes, keep((v) => (settings.hosting.idle_stop_minutes = v))}
						min={5}
						max={240}
						required
						disabled={locked('hosting.idle_stop_minutes')}
						hint="Only apps this task started and you did not pin."
						error={err('hosting', 'idle_stop_minutes')}
					/>
					<NumberField
						id="settings-max-running"
						label="Running apps at most"
						bind:value={() => settings.hosting.max_running_apps, keep((v) => (settings.hosting.max_running_apps = v))}
						min={1}
						max={20}
						required
						disabled={locked('hosting.max_running_apps')}
						hint="Also capped server-wide; extra starts queue."
						error={err('hosting', 'max_running_apps')}
					/>
				</div>
			</Card.Content>
		</Card.Root>

		<!-- Access -->
		<Card.Root class="gap-3">
			<Card.Header>
				<Card.Title>Access</Card.Title>
				<Card.Description>How participants get in. At least one method is needed to open the task.</Card.Description>
			</Card.Header>
			<Card.Content class="space-y-3">
				<label class="flex items-start gap-2.5 text-sm">
					<Checkbox class="mt-0.5" bind:checked={settings.access.allow_invite_links} disabled={locked('access.allow_invite_links')} />
					<span>
						Invite links
						<span class="block text-xs text-muted-foreground">Anonymous join links; each participant gets a resume code.</span>
					</span>
				</label>
				<label class="flex items-start gap-2.5 text-sm">
					<Checkbox class="mt-0.5" bind:checked={settings.access.allow_access_codes} disabled={locked('access.allow_access_codes')} />
					<span>
						Access codes
						<span class="block text-xs text-muted-foreground">One-person codes you generate and hand out.</span>
					</span>
				</label>
				<label class="flex items-start gap-2.5 text-sm">
					<Checkbox class="mt-0.5" bind:checked={settings.access.allow_accounts} disabled={locked('access.allow_accounts')} />
					<span>
						Evaluator accounts
						<span class="block text-xs text-muted-foreground">Restricted sign-in accounts that can only use the evaluation portal.</span>
					</span>
				</label>
				{#if accessCount === 0}
					<p class="flex items-start gap-1.5 text-[11px] text-warning-fg">
						<TriangleAlert class="mt-px size-3 shrink-0" aria-hidden="true" />No access method enabled — nobody can join.
					</p>
				{/if}
			</Card.Content>
		</Card.Root>

		<!-- Quality -->
		<Card.Root class="gap-3">
			<Card.Header>
				<Card.Title class="flex items-center gap-2">Data quality {@render frozenMark(qualityFrozen)}</Card.Title>
				<Card.Description>
					{qualityFrozen
						? 'Locked: the analysis plan excludes speeders, so this threshold decides which data count.'
						: 'Flags shown on participants and assignments; exclusions follow the analysis plan.'}
				</Card.Description>
			</Card.Header>
			<Card.Content>
				<div class="sm:w-64">
					<NumberField
						id="settings-min-seconds"
						label="Minimum seconds per item"
						bind:value={() => settings.quality.min_seconds_per_item, keep((v) => (settings.quality.min_seconds_per_item = v))}
						min={0}
						max={7200}
						required
						disabled={locked('quality.min_seconds_per_item')}
						hint="Faster submissions are flagged as speeding; 0 = off."
						error={err('quality', 'min_seconds_per_item')}
					/>
				</div>
			</Card.Content>
		</Card.Root>
	</div>
</div>
