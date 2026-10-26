<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import Code from '@lucide/svelte/icons/code';
	import Shield from '@lucide/svelte/icons/shield';
	import BarChart3 from '@lucide/svelte/icons/bar-chart-3';
	import Boxes from '@lucide/svelte/icons/boxes';

	const features = [
		{ icon: Code, title: 'Code Generation', desc: 'Generates full-stack web applications from LLM APIs with multiple model support.' },
		{ icon: Shield, title: 'Security Analysis', desc: 'Static and dynamic analysis to identify vulnerabilities in generated code.' },
		{ icon: BarChart3, title: 'Performance Testing', desc: 'Automated benchmarks to measure response times, throughput, and resource usage.' },
		{ icon: Boxes, title: 'Model Comparison', desc: 'Side-by-side rankings and statistics across models and benchmarks.' },
	];
</script>

<div class="max-w-3xl mx-auto space-y-4">
	<PageHeader
		title="LLM Lab"
		documentTitle="About"
		description="A research platform for evaluating and comparing LLMs: it generates web applications through LLM APIs, deploys them in isolated containers, and analyzes them — security audits, performance benchmarks, code quality checks and AI review."
	>
		{#snippet meta()}<Badge variant="outline" class="font-mono text-xs">v0.1.0</Badge>{/snippet}
		<div class="flex flex-wrap items-center gap-1.5">
			<span class="section-label mr-1">Stack</span>
			{#each ['Django', 'SvelteKit', 'Tailwind CSS', 'PostgreSQL', 'Celery', 'Docker', 'Redis'] as tech (tech)}
				<Badge variant="outline">{tech}</Badge>
			{/each}
		</div>
	</PageHeader>

	<Card.Root class="gap-0 py-0">
		<ul class="divide-y divide-border">
			{#each features as f (f.title)}
				<li class="flex items-start gap-3 px-4 py-3">
					<f.icon class="size-4 mt-0.5 text-muted-foreground shrink-0" aria-hidden="true" />
					<div>
						<p class="font-medium text-sm">{f.title}</p>
						<p class="text-sm text-muted-foreground">{f.desc}</p>
					</div>
				</li>
			{/each}
		</ul>
	</Card.Root>
</div>
