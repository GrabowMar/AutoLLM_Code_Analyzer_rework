<script lang="ts">
	import * as Dialog from '$lib/components/ui/dialog';
	import * as Select from '$lib/components/ui/select';
	import { Button } from '$lib/components/ui/button';
	import { Badge } from '$lib/components/ui/badge';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Switch } from '$lib/components/ui/switch';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { toast } from 'svelte-sonner';
	import Loader from '@lucide/svelte/icons/loader-circle';
	import Play from '@lucide/svelte/icons/play';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import CheckCircle2 from '@lucide/svelte/icons/check-circle-2';
	import XCircle from '@lucide/svelte/icons/circle-x';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import { severityColors } from '$lib/constants/analysis';
	import { numberInputValue } from '$lib/components/generator/llm-params';
	import {
		testTool,
		updateToolConfig,
		type AnalyzerTool,
		type ConfigField,
		type InstalledTool,
		type TestResult,
	} from '$lib/api/analyzers';

	interface Props {
		open: boolean;
		installed: InstalledTool | null;
		tool: AnalyzerTool | null;
		onsaved: (updated: InstalledTool) => void;
	}

	let { open = $bindable(false), installed, tool, onsaved }: Props = $props();

	let draft = $state<Record<string, unknown>>({});
	let saving = $state(false);
	let testing = $state(false);
	let testResult = $state<TestResult | null>(null);

	const fields = $derived(tool?.config_schema ?? []);

	// Reset the draft each time the dialog opens for a tool.
	$effect(() => {
		if (open && installed) {
			draft = { ...(installed.config ?? {}) };
			testResult = null;
		}
	});

	const dirty = $derived(JSON.stringify(draft) !== JSON.stringify(installed?.config ?? {}));

	function valueOf(field: ConfigField): unknown {
		return draft[field.name] ?? field.default;
	}

	function set(name: string, value: unknown) {
		draft = { ...draft, [name]: value };
	}

	function toggleOption(field: ConfigField, option: string, on: boolean) {
		const current = Array.isArray(valueOf(field)) ? (valueOf(field) as string[]) : [];
		set(field.name, on ? [...new Set([...current, option])] : current.filter((v) => v !== option));
	}

	function fieldError(field: ConfigField): string {
		const v = valueOf(field);
		const empty = v === null || v === undefined || v === '' || (Array.isArray(v) && v.length === 0);
		if (field.required && empty) return 'Required';
		if (field.type === 'number' && typeof v === 'number') {
			if (field.min !== null && v < field.min) return `Must be at least ${field.min}`;
			if (field.max !== null && v > field.max) return `Must be at most ${field.max}`;
		}
		return '';
	}

	const invalid = $derived(fields.some((f) => fieldError(f) !== ''));

	function resetDefaults() {
		draft = { ...(tool?.default_config ?? {}) };
		testResult = null;
	}

	async function save() {
		if (!installed) return;
		saving = true;
		try {
			onsaved(await updateToolConfig(installed.tool_slug, draft));
			toast.success('Configuration saved');
			open = false;
		} catch {
			toast.error('Failed to save configuration');
		} finally {
			saving = false;
		}
	}

	async function runTest() {
		if (!installed) return;
		testing = true;
		testResult = null;
		try {
			testResult = await testTool(installed.tool_slug, draft);
		} catch (e: any) {
			toast.error(e?.detail || 'Test failed');
		} finally {
			testing = false;
		}
	}
</script>

<Dialog.Root bind:open>
	<Dialog.Content class="max-h-[90vh] max-w-lg overflow-y-auto">
		<Dialog.Header>
			<Dialog.Title>{installed?.tool_name}</Dialog.Title>
			<Dialog.Description>
				Configure options and run the tool against a sample snippet.
				{#if tool?.docs_url}
					<a
						href={tool.docs_url}
						target="_blank"
						rel="noopener noreferrer"
						class="inline-flex items-center gap-0.5 text-primary hover:underline"
						aria-label="{installed?.tool_name} documentation (opens in new tab)"
					>
						Docs <ExternalLink class="size-3" />
					</a>
				{/if}
			</Dialog.Description>
		</Dialog.Header>

		<div class="space-y-4">
			{#if fields.length > 0}
				{#each fields as field (field.name)}
					{@const error = fieldError(field)}
					<div class="space-y-1.5">
						{#if field.type === 'boolean'}
							<div class="flex items-center justify-between gap-3">
								<Label for="cfg-{field.name}">{field.label}</Label>
								<Switch
									id="cfg-{field.name}"
									checked={!!valueOf(field)}
									onCheckedChange={(v) => set(field.name, v)}
								/>
							</div>
						{:else if field.type === 'select'}
							<Label for="cfg-{field.name}">{field.label}</Label>
							<Select.Simple
								id="cfg-{field.name}"
								value={String(valueOf(field) ?? '')}
								items={field.options}
								onValueChange={(v) => set(field.name, v)}
							/>
						{:else if field.type === 'multiselect'}
							<span class="text-sm font-medium" id="cfg-{field.name}-label">{field.label}</span>
							<div class="flex flex-wrap gap-x-4 gap-y-2" role="group" aria-labelledby="cfg-{field.name}-label">
								{#each field.options as opt (opt.value)}
									<label class="flex items-center gap-2 text-sm">
										<Checkbox
											checked={Array.isArray(valueOf(field)) && (valueOf(field) as string[]).includes(opt.value)}
											onCheckedChange={(v) => toggleOption(field, opt.value, v === true)}
										/>
										{opt.label}
									</label>
								{/each}
							</div>
						{:else if field.type === 'number'}
							<Label for="cfg-{field.name}">{field.label}</Label>
							<Input
								id="cfg-{field.name}"
								type="number"
								min={field.min ?? undefined}
								max={field.max ?? undefined}
								placeholder={field.placeholder}
								aria-invalid={error !== ''}
								value={numberInputValue(valueOf(field))}
								oninput={(e) => {
									const n = e.currentTarget.valueAsNumber;
									set(field.name, Number.isNaN(n) ? null : n);
								}}
							/>
						{:else}
							<Label for="cfg-{field.name}">{field.label}</Label>
							<Input
								id="cfg-{field.name}"
								placeholder={field.placeholder}
								aria-invalid={error !== ''}
								value={String(valueOf(field) ?? '')}
								oninput={(e) => set(field.name, e.currentTarget.value)}
							/>
						{/if}
						{#if error}
							<p class="text-xs text-destructive">{error}</p>
						{:else if field.description}
							<p class="text-xs text-muted-foreground">{field.description}</p>
						{/if}
					</div>
				{/each}
			{:else}
				<p class="text-sm text-muted-foreground">This tool has no configurable options.</p>
			{/if}

			{#if testResult}
				<div class="rounded-md border p-3 text-sm" aria-live="polite">
					<div class="flex items-center gap-2 font-medium">
						{#if testResult.available}
							<CheckCircle2 class="size-4 text-success-fg" /> Available
						{:else}
							<XCircle class="size-4 text-destructive" /> Unavailable
						{/if}
					</div>
					<p class="mt-1 text-xs text-muted-foreground">{testResult.message}</p>
					{#if testResult.findings.length === 0 && Object.keys(testResult.metrics ?? {}).length === 0}
						<p class="mt-2 text-xs text-muted-foreground">The sample produced no findings.</p>
					{/if}
					{#if testResult.findings.length > 0}
						<p class="mt-2 text-xs">Sample run produced {testResult.findings.length} finding(s):</p>
						<ul class="mt-1 space-y-1">
							{#each testResult.findings.slice(0, 5) as f, i (i)}
								<li class="flex items-center gap-2 text-xs">
									<Badge class={severityColors[(f.severity as string) ?? 'info'] ?? ''}>
										{f.severity}
									</Badge>
									<span class="truncate">{f.title}</span>
								</li>
							{/each}
						</ul>
					{/if}
					{#if testResult.metrics && Object.keys(testResult.metrics).length > 0}
						<p class="mt-2 text-xs">Metrics:</p>
						<dl class="mt-1 grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5 text-xs">
							{#each Object.entries(testResult.metrics).filter(([, v]) => typeof v !== 'object') as [key, value] (key)}
								<dt class="text-muted-foreground">{key.replace(/_/g, ' ')}</dt>
								<dd class="font-mono">{value}</dd>
							{/each}
						</dl>
					{/if}
				</div>
			{/if}
		</div>

		<Dialog.Footer class="gap-2 sm:justify-between">
			<Button variant="ghost" size="sm" onclick={resetDefaults} disabled={fields.length === 0}>
				<RotateCcw class="size-4" /> Reset to defaults
			</Button>
			<div class="flex gap-2">
				<Button variant="outline" size="sm" onclick={runTest} disabled={testing || invalid}>
					{#if testing}<Loader class="size-4 animate-spin motion-reduce:animate-none" />{:else}<Play class="size-4" />{/if}
					Test
				</Button>
				<Button size="sm" onclick={save} disabled={saving || invalid || !dirty}>
					{#if saving}<Loader class="size-4 animate-spin motion-reduce:animate-none" />{/if}
					Save
				</Button>
			</div>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>
