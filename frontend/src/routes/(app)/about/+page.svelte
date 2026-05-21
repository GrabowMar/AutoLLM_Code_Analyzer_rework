<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import FlaskConical from '@lucide/svelte/icons/flask-conical';
	import Code from '@lucide/svelte/icons/code';
	import Shield from '@lucide/svelte/icons/shield';
	import BarChart3 from '@lucide/svelte/icons/bar-chart-3';
	import Boxes from '@lucide/svelte/icons/boxes';
</script>

<svelte:head>
	<title>About - LLM Lab</title>
</svelte:head>

<div class="max-w-3xl mx-auto space-y-8">
	<div class="page-header">
		<div class="flex items-center gap-4">
			<div class="flex size-12 items-center justify-center rounded-md bg-primary/10 text-primary border border-primary/20 shrink-0">
				<FlaskConical class="size-6" />
			</div>
			<div class="flex-1 min-w-0">
				<h1>LLM Lab</h1>
				<p>Research Platform for LLM Evaluation</p>
			</div>
			<Badge variant="outline" class="font-mono text-xs">v0.1.0</Badge>
		</div>
	</div>

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
