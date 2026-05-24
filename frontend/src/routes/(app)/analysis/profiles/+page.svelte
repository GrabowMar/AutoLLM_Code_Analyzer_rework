<script lang="ts">
import { Button } from '$lib/components/ui/button';
import * as Card from '$lib/components/ui/card';
import * as Dialog from '$lib/components/ui/dialog';
import { Badge } from '$lib/components/ui/badge';
import BookmarkCheck from '@lucide/svelte/icons/bookmark-check';
import Plus from '@lucide/svelte/icons/plus';
import Loader from '@lucide/svelte/icons/loader-circle';
import Star from '@lucide/svelte/icons/star';
import AnalysisProfileCard from '$lib/components/analysis/AnalysisProfileCard.svelte';
import AnalyzerSelector from '$lib/components/analysis/AnalyzerSelector.svelte';
import AnalyzerConfigForm from '$lib/components/analysis/AnalyzerConfigForm.svelte';
import {
	getAnalysisProfiles,
	createAnalysisProfile,
	updateAnalysisProfile,
	deleteAnalysisProfile,
	getAnalyzers,
	type AnalysisProfile,
	type AnalyzerInfo,
} from '$lib/api/analysis';
import { toast } from 'svelte-sonner';

let profiles = $state<AnalysisProfile[]>([]);
let analyzers = $state<AnalyzerInfo[]>([]);
let loading = $state(true);
let analyzersLoading = $state(true);
let savingProfile = $state(false);

// Modal state
let showModal = $state(false);
let editingProfile = $state<AnalysisProfile | null>(null);

// Form state
let formName = $state('');
let formDescription = $state('');
let formIsDefault = $state(false);
let formSelectedAnalyzers = $state(new Set<string>());
let formAnalyzerConfigs = $state<Record<string, Record<string, unknown>>>({});

async function loadData() {
	loading = true;
	try {
		[profiles, analyzers] = await Promise.all([getAnalysisProfiles(), getAnalyzers()]);
	} catch {
		toast.error('Failed to load profiles');
	} finally {
		loading = false;
		analyzersLoading = false;
	}
}

$effect(() => {
	loadData();
});

function openCreate() {
	editingProfile = null;
	formName = '';
	formDescription = '';
	formIsDefault = false;
	formSelectedAnalyzers = new Set();
	formAnalyzerConfigs = {};
	showModal = true;
}

function openEdit(profile: AnalysisProfile) {
	editingProfile = profile;
	formName = profile.name;
	formDescription = profile.description;
	formIsDefault = profile.is_default;
	formSelectedAnalyzers = new Set(profile.analyzers);
	// Deep-copy settings
	formAnalyzerConfigs = Object.fromEntries(
		Object.entries(profile.settings).map(([k, v]) => [k, { ...v }])
	);
	showModal = true;
}

function toggleAnalyzer(name: string) {
	if (formSelectedAnalyzers.has(name)) {
		formSelectedAnalyzers.delete(name);
	} else {
		formSelectedAnalyzers.add(name);
	}
	formSelectedAnalyzers = new Set(formSelectedAnalyzers);
}

function handleConfigChange(analyzerName: string, key: string, value: unknown) {
	formAnalyzerConfigs = {
		...formAnalyzerConfigs,
		[analyzerName]: { ...(formAnalyzerConfigs[analyzerName] ?? {}), [key]: value },
	};
}

async function handleSave() {
	if (!formName.trim()) {
		toast.error('Profile name is required');
		return;
	}
	savingProfile = true;
	try {
		const data = {
			name: formName.trim(),
			description: formDescription,
			analyzers: Array.from(formSelectedAnalyzers),
			settings: formAnalyzerConfigs,
			is_default: formIsDefault,
		};
		if (editingProfile) {
			const updated = await updateAnalysisProfile(editingProfile.id, data);
			profiles = profiles.map((p) => (p.id === updated.id ? updated : p));
			toast.success('Profile updated');
		} else {
			const created = await createAnalysisProfile(data);
			profiles = [...profiles, created];
			toast.success('Profile created');
		}
		showModal = false;
	} catch {
		toast.error('Failed to save profile');
	} finally {
		savingProfile = false;
	}
}

async function handleDelete(profile: AnalysisProfile) {
	if (!confirm(`Delete profile "${profile.name}"?`)) return;
	try {
		await deleteAnalysisProfile(profile.id);
		profiles = profiles.filter((p) => p.id !== profile.id);
		toast.success('Profile deleted');
	} catch {
		toast.error('Failed to delete profile');
	}
}

const selectedAnalyzerInfos = $derived(
	analyzers.filter((a) => formSelectedAnalyzers.has(a.name))
);
</script>

<div class="space-y-6">
	<div class="flex items-center justify-between">
		<div class="flex items-center gap-3">
			<BookmarkCheck class="h-6 w-6 text-emerald-500" />
			<div>
				<h1 class="text-xl font-semibold">Analysis Profiles</h1>
				<p class="text-sm text-muted-foreground">Save and reuse analyzer configurations.</p>
			</div>
		</div>
		<Button onclick={openCreate}>
			<Plus class="mr-2 h-4 w-4" /> New Profile
		</Button>
	</div>

	{#if loading}
		<div class="flex items-center justify-center py-16 text-muted-foreground">
			<Loader class="mr-2 h-5 w-5 animate-spin" /> Loading profiles…
		</div>
	{:else if profiles.length === 0}
		<Card.Root>
			<Card.Content class="flex flex-col items-center gap-3 py-12 text-center">
				<BookmarkCheck class="h-10 w-10 text-muted-foreground/40" />
				<p class="text-sm text-muted-foreground">No profiles yet. Create one to save a set of analyzers and settings for reuse.</p>
				<Button onclick={openCreate}><Plus class="mr-2 h-4 w-4" /> Create Profile</Button>
			</Card.Content>
		</Card.Root>
	{:else}
		<div class="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
			{#each profiles as profile}
				<AnalysisProfileCard {profile} onEdit={openEdit} onDelete={handleDelete} />
			{/each}
		</div>
	{/if}
</div>

<!-- Create/Edit Modal -->
<Dialog.Root bind:open={showModal}>
	<Dialog.Content class="max-w-2xl max-h-[90vh] overflow-y-auto">
		<Dialog.Header>
			<Dialog.Title>{editingProfile ? 'Edit Profile' : 'New Profile'}</Dialog.Title>
		</Dialog.Header>

		<div class="space-y-4 pt-2">
			<!-- Name & Description -->
			<div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
				<div>
					<label class="mb-1 block text-sm font-medium">Name *</label>
					<input
						type="text"
						class="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
						placeholder="e.g. Quick Security Scan"
						bind:value={formName}
					/>
				</div>
				<div class="flex items-center gap-2 pt-6">
					<input type="checkbox" id="isDefault" class="rounded" bind:checked={formIsDefault} />
					<label for="isDefault" class="flex items-center gap-1.5 text-sm cursor-pointer">
						<Star class="h-3.5 w-3.5 text-amber-400" /> Set as default profile
					</label>
				</div>
			</div>
			<div>
				<label class="mb-1 block text-sm font-medium">Description</label>
				<input
					type="text"
					class="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
					placeholder="Optional description…"
					bind:value={formDescription}
				/>
			</div>

			<!-- Analyzer selection -->
			<div>
				<h3 class="mb-2 text-sm font-medium">Analyzers</h3>
				<AnalyzerSelector
					{analyzersLoading}
					analyzersError=""
					{analyzers}
					selectedAnalyzers={formSelectedAnalyzers}
					onToggleAnalyzer={toggleAnalyzer}
					onSelectAll={() => { formSelectedAnalyzers = new Set(analyzers.map((a) => a.name)); }}
					onClearAll={() => { formSelectedAnalyzers = new Set(); }}
					onReload={() => {}}
				/>
			</div>

			<!-- Per-analyzer config -->
			{#if selectedAnalyzerInfos.length > 0}
				<div class="space-y-3">
					<h3 class="text-sm font-medium">Analyzer Configuration</h3>
					{#each selectedAnalyzerInfos as analyzer}
						<details class="rounded-md border border-border">
							<summary class="flex cursor-pointer items-center justify-between px-3 py-2 text-sm font-medium hover:bg-muted/30">
								{analyzer.display_name}
								<Badge variant="outline" class="text-xs ml-2">{analyzer.type}</Badge>
							</summary>
							<div class="border-t border-border px-3 py-3">
								<AnalyzerConfigForm
									{analyzer}
									config={formAnalyzerConfigs[analyzer.name] ?? {}}
									onConfigChange={(key, val) => handleConfigChange(analyzer.name, key, val)}
								/>
							</div>
						</details>
					{/each}
				</div>
			{/if}
		</div>

		<Dialog.Footer class="pt-2">
			<Button variant="outline" onclick={() => { showModal = false; }}>Cancel</Button>
			<Button disabled={savingProfile} onclick={handleSave}>
				{#if savingProfile}<Loader class="mr-2 h-3 w-3 animate-spin" />{/if}
				{editingProfile ? 'Save Changes' : 'Create Profile'}
			</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>
