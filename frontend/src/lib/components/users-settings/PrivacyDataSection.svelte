<script lang="ts">
	/**
	 * PrivacyDataSection — an honest list of what the app keeps in the browser
	 * (essential cookies and local preferences only) plus export, import and
	 * reset of those preferences.
	 */
	import { toast } from 'svelte-sonner';
	import { modeStorageKey } from 'mode-watcher';
	import { getPreferences } from '$lib/stores/preferences.svelte';
	import SettingsBlock from './SettingsBlock.svelte';
	import SettingsRow from './SettingsRow.svelte';
	import { Button } from '$lib/components/ui/button';
	import { Label } from '$lib/components/ui/label';
	import { Textarea } from '$lib/components/ui/textarea';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import Download from '@lucide/svelte/icons/download';
	import Upload from '@lucide/svelte/icons/upload';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import FileUp from '@lucide/svelte/icons/file-up';

	const prefs = getPreferences();

	const STORED = $derived([
		{ name: 'sessionid', where: 'Cookie', purpose: 'Keeps you signed in.', essential: true },
		{ name: 'csrftoken', where: 'Cookie', purpose: 'Protects forms against cross-site requests.', essential: true },
		{ name: 'cookie_consent', where: 'Cookie', purpose: 'Remembers that you dismissed the cookie notice.', essential: true },
		{ name: 'llm-lab-preferences', where: 'Local storage', purpose: 'Sidebar, table density and avatar color.', essential: false },
		{ name: modeStorageKey.current, where: 'Local storage', purpose: 'Light, dark or system theme.', essential: false },
	]);

	let importOpen = $state(false);
	let importJson = $state('');
	let resetOpen = $state(false);
	let fileInput = $state<HTMLInputElement | null>(null);

	function exportPrefs() {
		const blob = new Blob([prefs.exportPreferences()], { type: 'application/json' });
		const url = URL.createObjectURL(blob);
		const a = document.createElement('a');
		a.href = url;
		a.download = 'llm-lab-preferences.json';
		a.click();
		URL.revokeObjectURL(url);
	}

	function applyImport(json: string) {
		if (prefs.importPreferences(json)) {
			toast.success('Preferences imported.');
			importJson = '';
			importOpen = false;
		} else {
			toast.error('That is not a preferences export (expected a JSON object).');
		}
	}

	async function importFile(e: Event) {
		const file = (e.currentTarget as HTMLInputElement).files?.[0];
		if (!file) return;
		applyImport(await file.text());
		(e.currentTarget as HTMLInputElement).value = '';
	}

	function reset() {
		prefs.resetPreferences();
		resetOpen = false;
		toast.success('Preferences reset to defaults.');
	}
</script>

<SettingsBlock title="Stored in this browser">
	{#snippet description()}
		No analytics or advertising cookies. Your work (applications, analyses, keys) lives on the server; see the
		<a href="/privacy" class="settings-link">privacy policy</a>.
	{/snippet}
	{#each STORED as item (item.name)}
		<div class="settings-row flex items-start justify-between gap-4">
			<div class="min-w-0 space-y-0.5">
				<p class="truncate font-mono text-[13px]">{item.name}</p>
				<p class="text-[13px] leading-5 text-muted-foreground">{item.purpose}</p>
			</div>
			<span class="shrink-0 pt-0.5 text-xs text-muted-foreground">
				{item.where}{#if item.essential}<span class="ml-1.5 rounded-full border border-border px-1.5 py-px">essential</span>{/if}
			</span>
		</div>
	{/each}
</SettingsBlock>

<SettingsBlock
	title="Preferences backup"
	description="Theme, sidebar, table density and avatar color, as a JSON file you can load in another browser."
>
	<SettingsRow label="Export" description="Download your preferences.">
		<Button variant="outline" size="sm" onclick={exportPrefs}><Download />Export</Button>
	</SettingsRow>
	<SettingsRow label="Import" description="Load a file you exported, or paste its contents.">
		<div class="flex gap-2">
			<Button variant="ghost" size="sm" onclick={() => (importOpen = !importOpen)} aria-expanded={importOpen}>
				<Upload />Paste
			</Button>
			<Button variant="outline" size="sm" onclick={() => fileInput?.click()}><FileUp />Choose file…</Button>
		</div>
		<input
			bind:this={fileInput}
			type="file"
			accept="application/json,.json"
			class="hidden"
			onchange={importFile}
			aria-hidden="true"
			tabindex="-1"
		/>
	</SettingsRow>
	{#if importOpen}
		<div class="settings-row space-y-2 bg-muted/30">
			<Label for="import-json">Preferences JSON</Label>
			<Textarea
				id="import-json"
				class="min-h-28 bg-card font-mono text-xs"
				placeholder={'{ "theme": "dark", "compactTables": true, … }'}
				bind:value={importJson}
			/>
			<div class="flex justify-end gap-2">
				<Button size="sm" variant="ghost" onclick={() => ((importOpen = false), (importJson = ''))}>Cancel</Button>
				<Button size="sm" onclick={() => applyImport(importJson)} disabled={!importJson.trim()}>Import</Button>
			</div>
		</div>
	{/if}
	<SettingsRow label="Reset" description="Return every preference in this browser to its default.">
		<Button
			variant="outline"
			size="sm"
			class="text-danger-fg hover:border-destructive/50 hover:bg-destructive/10 hover:text-danger-fg"
			onclick={() => (resetOpen = true)}
		>
			<RotateCcw />Reset
		</Button>
	</SettingsRow>
</SettingsBlock>

<ConfirmDialog
	bind:open={resetOpen}
	title="Reset preferences?"
	description="Theme, sidebar, table density and avatar color return to their defaults in this browser. Your account, keys and tokens are not affected."
	confirmLabel="Reset"
	destructive
	onConfirm={reset}
/>
