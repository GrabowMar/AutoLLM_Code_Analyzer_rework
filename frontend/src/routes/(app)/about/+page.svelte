<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import Code from '@lucide/svelte/icons/code';
	import Shield from '@lucide/svelte/icons/shield';
	import BarChart3 from '@lucide/svelte/icons/bar-chart-3';
	import Boxes from '@lucide/svelte/icons/boxes';
</script>

<div class="max-w-3xl mx-auto space-y-6">
	<PageHeader title="LLM Lab" documentTitle="About" description="Research Platform for LLM Evaluation">
		{#snippet meta()}<Badge variant="outline" class="font-mono text-xs">v0.1.0</Badge>{/snippet}
	</PageHeader>

	<Card.Root>
		<Card.Content class="pt-6">
			<p class="text-base leading-relaxed text-muted-foreground">
				LLM Lab is a research platform for evaluating and comparing large language models.
				It automates the generation of web applications using LLM APIs, deploys them in isolated containers,
				and runs comprehensive analysis — including security audits, performance benchmarks, code quality checks, and AI review.
			</p>
		</Card.Content>
	</Card.Root>

	<div class="grid gap-4 sm:grid-cols-2">
		<Card.Root>
			<Card.Content class="pt-6 flex items-start gap-3">
				<Code class="size-5 mt-0.5 text-muted-foreground shrink-0" />
				<div>
					<p class="font-medium text-sm">Code Generation</p>
					<p class="text-sm text-muted-foreground">Generates full-stack web applications from LLM APIs with multiple model support.</p>
				</div>
			</Card.Content>
		</Card.Root>
		<Card.Root>
			<Card.Content class="pt-6 flex items-start gap-3">
				<Shield class="size-5 mt-0.5 text-muted-foreground shrink-0" />
				<div>
					<p class="font-medium text-sm">Security Analysis</p>
					<p class="text-sm text-muted-foreground">Static and dynamic analysis to identify vulnerabilities in generated code.</p>
				</div>
			</Card.Content>
		</Card.Root>
		<Card.Root>
			<Card.Content class="pt-6 flex items-start gap-3">
				<BarChart3 class="size-5 mt-0.5 text-muted-foreground shrink-0" />
				<div>
					<p class="font-medium text-sm">Performance Testing</p>
					<p class="text-sm text-muted-foreground">Automated benchmarks to measure response times, throughput, and resource usage.</p>
				</div>
			</Card.Content>
		</Card.Root>
		<Card.Root>
			<Card.Content class="pt-6 flex items-start gap-3">
				<Boxes class="size-5 mt-0.5 text-muted-foreground shrink-0" />
				<div>
					<p class="font-medium text-sm">Model Comparison</p>
					<p class="text-sm text-muted-foreground">Side-by-side rankings and statistics across models and benchmarks.</p>
				</div>
			</Card.Content>
		</Card.Root>
	</div>

	<Card.Root>
		<Card.Content class="pt-6">
			<h2 class="text-sm font-medium mb-3">Tech Stack</h2>
			<div class="flex flex-wrap gap-2">
				<Badge variant="outline">Django</Badge>
				<Badge variant="outline">SvelteKit</Badge>
				<Badge variant="outline">Tailwind CSS</Badge>
				<Badge variant="outline">PostgreSQL</Badge>
				<Badge variant="outline">Celery</Badge>
				<Badge variant="outline">Docker</Badge>
				<Badge variant="outline">Redis</Badge>
			</div>
		</Card.Content>
	</Card.Root>
</div>
