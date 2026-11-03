<script lang="ts">
	/**
	 * /library/prompts/playground — render a prompt set against any cell and diff two renders.
	 * `?set=<prompt-set ref>` and `?task=<task ref>` preselect.
	 */
	import { page } from '$app/state';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import { PromptPlayground } from '$lib/components/library';
	import { parseRef } from '$lib/api/v3/types';

	const set = page.url.searchParams.get('set');
	const task = page.url.searchParams.get('task');
	const initialSet = set && parseRef(set) ? set : null;
	const initial = task && parseRef(task) ? { task, variant: 'standard' as const, auth_mode: 'contract_bearer' as const } : null;
</script>

<div class="space-y-4">
	<PageHeader
		title="Prompt playground"
		description="Render a prompt set with any task, stack, style, auth mode and guidance, see which library entity owns each part of the text, and compare two cells."
		back={{ href: '/library/prompts', label: 'Prompt templates' }}
	>
		{#snippet help()}
			<p>
				This is the same renderer the study plan uses (sandboxed Jinja, a fixed context). The context never contains numbers from the Budget
				preset, the seed or any id, so the prompt hash depends only on the levels shown here.
			</p>
		{/snippet}
	</PageHeader>
	<PromptPlayground {initial} {initialSet} />
</div>
