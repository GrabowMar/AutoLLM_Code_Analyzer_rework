<script lang="ts">
	/**
	 * StackProfileEditor — `StackProfileContent`: choice mode, backend (language, framework, runtime),
	 * frontend (framework, language, build tool), database, run contract, the brief fragment (a pinned
	 * prompt-template ref), adherence markers and the minimum sandbox resources.
	 *
	 * Props: `value`, `onchange`, `readonly`.
	 */
	import * as Select from '$lib/components/ui/select';
	import { Input } from '$lib/components/ui/input';
	import { Segmented } from '$lib/components/ui/segmented';
	import type { StackProfileContent } from '$lib/api/v3/types';
	import FormRow from './FormRow.svelte';
	import ListInput from './ListInput.svelte';
	import NumberInput from './NumberInput.svelte';
	import LibraryRefPicker from './LibraryRefPicker.svelte';

	interface Props {
		value: StackProfileContent;
		onchange: (v: StackProfileContent) => void;
		readonly?: boolean;
	}

	let { value, onchange, readonly = false }: Props = $props();

	const items = (xs: string[]) => xs.map((x) => ({ value: x, label: x }));
	const set = (patch: Partial<StackProfileContent>) => onchange({ ...value, ...patch });
	const setBackend = (patch: Partial<StackProfileContent['backend']>) => set({ backend: { ...value.backend, ...patch } });
	const setFrontend = (patch: Partial<StackProfileContent['frontend']>) => set({ frontend: { ...value.frontend, ...patch } });
	const setContract = (patch: Partial<StackProfileContent['run_contract']>) => set({ run_contract: { ...value.run_contract, ...patch } });
	const setAdherence = (patch: Partial<StackProfileContent['adherence']>) => set({ adherence: { ...value.adherence, ...patch } });
	const setResources = (patch: Partial<StackProfileContent['resources']>) => set({ resources: { ...value.resources, ...patch } });
</script>

<div class="space-y-5">
	<FormRow label="Choice" inline help="Fixed: the model must use this stack. Free: the model chooses (allowed bases are listed in the brief).">
		<Segmented aria-label="Choice mode" value={value.choice_mode} disabled={readonly} items={[{ value: 'fixed', label: 'Fixed stack' }, { value: 'free', label: 'Free choice' }]} onValueChange={(v) => set({ choice_mode: v as 'fixed' | 'free' })} />
	</FormRow>

	<div class="grid gap-4 md:grid-cols-3">
		<FormRow label="Backend language">
			<Select.Simple value={value.backend.language} items={items(['python', 'javascript', 'typescript', 'go', 'java', 'free'])} disabled={readonly} onValueChange={(v) => setBackend({ language: v as StackProfileContent['backend']['language'] })} />
		</FormRow>
		<FormRow label="Backend framework">
			<Select.Simple value={value.backend.framework} items={items(['flask', 'fastapi', 'django', 'express', 'go-stdlib', 'gin', 'spring-boot', 'free'])} disabled={readonly} onValueChange={(v) => setBackend({ framework: v as StackProfileContent['backend']['framework'] })} />
		</FormRow>
		<FormRow label="Runtime" for="stack-runtime" help="python3.13, node24, go1.25, temurin21 or free.">
			<Input id="stack-runtime" value={value.backend.runtime} oninput={(e) => setBackend({ runtime: e.currentTarget.value })} class="font-mono" readonly={readonly} />
		</FormRow>
		<FormRow label="Frontend framework">
			<Select.Simple value={value.frontend.framework} items={items(['react', 'vue', 'svelte', 'none', 'free'])} disabled={readonly} onValueChange={(v) => setFrontend({ framework: v as StackProfileContent['frontend']['framework'] })} />
		</FormRow>
		<FormRow label="Frontend language">
			<Select.Simple value={value.frontend.language} items={items(['ts', 'js', 'free', 'none'])} disabled={readonly} onValueChange={(v) => setFrontend({ language: v as StackProfileContent['frontend']['language'] })} />
		</FormRow>
		<FormRow label="Build tool">
			<Select.Simple value={value.frontend.build_tool} items={items(['vite', 'none', 'free'])} disabled={readonly} onValueChange={(v) => setFrontend({ build_tool: v as StackProfileContent['frontend']['build_tool'] })} />
		</FormRow>
		<FormRow label="Database">
			<Select.Simple value={value.database} items={items(['sqlite', 'postgres', 'free'])} disabled={readonly} onValueChange={(v) => set({ database: v as StackProfileContent['database'] })} />
		</FormRow>
	</div>

	<section class="space-y-3" aria-labelledby="stack-contract">
		<h3 id="stack-contract" class="section-label">Run contract</h3>
		<div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
			<FormRow label="Port"><NumberInput value={value.run_contract.port} min={1} max={65535} step={1} {readonly} onchange={(n) => n !== null && setContract({ port: n })} /></FormRow>
			<FormRow label="API prefix"><Input value={value.run_contract.api_prefix} oninput={(e) => setContract({ api_prefix: e.currentTarget.value })} class="h-8 font-mono" readonly={readonly} /></FormRow>
			<FormRow label="Health path"><Input value={value.run_contract.health_path} oninput={(e) => setContract({ health_path: e.currentTarget.value })} class="h-8 font-mono" readonly={readonly} /></FormRow>
			<FormRow label="Static serving">
				<Select.Simple value={value.run_contract.static_serving} items={items(['backend', 'none'])} size="sm" disabled={readonly} onValueChange={(v) => setContract({ static_serving: v as 'backend' | 'none' })} />
			</FormRow>
			<FormRow label="Data dir"><Input value={value.run_contract.data_dir} oninput={(e) => setContract({ data_dir: e.currentTarget.value })} class="h-8 font-mono" readonly={readonly} /></FormRow>
		</div>
	</section>

	<FormRow label="Brief fragment" inline help="The prompt template (role fragment) that describes this stack to the model. Its text is hashed into the treatment.">
		<LibraryRefPicker
			kind="prompt"
			value={value.brief_fragment ? [value.brief_fragment] : []}
			filter={(i) => (i.content as { role?: string }).role === 'fragment'}
			disabled={readonly}
			onchange={(r) => r[0] && set({ brief_fragment: r[0] })}
		/>
	</FormRow>

	<section class="space-y-3" aria-labelledby="stack-adherence">
		<h3 id="stack-adherence" class="section-label">Adherence markers</h3>
		<p class="text-xs text-muted-foreground">Hints for the adherence checker: files or imports that show the app really uses this stack.</p>
		<div class="grid gap-4 md:grid-cols-3">
			<FormRow label="Backend"><ListInput value={value.adherence.backend_markers} {readonly} onchange={(v) => setAdherence({ backend_markers: v })} /></FormRow>
			<FormRow label="Frontend"><ListInput value={value.adherence.frontend_markers} {readonly} onchange={(v) => setAdherence({ frontend_markers: v })} /></FormRow>
			<FormRow label="Database"><ListInput value={value.adherence.db_markers} {readonly} onchange={(v) => setAdherence({ db_markers: v })} /></FormRow>
		</div>
	</section>

	<section class="space-y-3" aria-labelledby="stack-resources">
		<h3 id="stack-resources" class="section-label">Minimum resources</h3>
		<p class="text-xs text-muted-foreground">A study's sandbox gets the maximum over its stacks' minimums and the Budget preset's values (applied to every cell).</p>
		<div class="grid gap-4 sm:grid-cols-2">
			<FormRow label="Memory" help="e.g. 2g, 3g for a Postgres sidecar.">
				<Input value={value.resources.min_memory} oninput={(e) => /^[1-9][0-9]*[kmg]$/.test(e.currentTarget.value) && setResources({ min_memory: e.currentTarget.value })} class="h-8 w-32 font-mono" readonly={readonly} />
			</FormRow>
			<FormRow label="CPUs"><NumberInput value={value.resources.min_cpus} min={0.25} step={0.25} {readonly} class="w-32" onchange={(n) => n !== null && n > 0 && setResources({ min_cpus: n })} /></FormRow>
		</div>
	</section>
</div>
