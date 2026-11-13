<script lang="ts">
	import type { Snippet } from 'svelte';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import ArrowUpRight from '@lucide/svelte/icons/arrow-up-right';
	import Code from '@lucide/svelte/icons/code';
	import ScanLine from '@lucide/svelte/icons/scan-line';
	import GitCompareArrows from '@lucide/svelte/icons/git-compare-arrows';
	import BrandMark from '$lib/components/BrandMark.svelte';
	import ThemeToggle from '$lib/components/ThemeToggle.svelte';
	import AsciiField from '$lib/components/dashboard/AsciiField.svelte';

	let { children }: { children: Snippet } = $props();
</script>

<div class="signup-shell">
	<header class="signup-topbar">
		<a href="/" aria-label="LLM Lab home" class="brand-link"><BrandMark size="lg" /></a>
		<div class="topbar-actions">
			<span class="research-label">A workspace for better evidence</span>
			<ThemeToggle />
		</div>
	</header>

	<div class="signup-content">
		<aside class="signup-story" aria-labelledby="signup-story-title">
			<AsciiField fadeFrom={null} class="story-field" />
			<div class="story-copy">
				<p class="eyebrow"><span class="edition-mark" aria-hidden="true"></span> Build. Test. Understand.</p>
				<h2 id="signup-story-title">Great code starts<br />with <em>good questions.</em></h2>
				<p class="story-description">Turn LLM-generated code into evidence you can trust. One workspace to generate, evaluate, and compare.</p>
			</div>

			<div class="field-panel">
				<div class="field-caption"><span>FROM PROMPT TO PROOF</span><span aria-hidden="true">↗</span></div>
				<div class="field-symbol" aria-hidden="true">&#123; <span>?</span> &#125;</div>
				<span class="field-note">Explore the possibilities.</span>
			</div>

			<ol class="story-steps">
				<li><span class="step-number">01</span><Code aria-hidden="true" /><span><strong>Generate</strong><small>Bring your models.</small></span></li>
				<li><span class="step-number">02</span><ScanLine aria-hidden="true" /><span><strong>Evaluate</strong><small>Inspect every angle.</small></span></li>
				<li><span class="step-number">03</span><GitCompareArrows aria-hidden="true" /><span><strong>Compare</strong><small>Find what works.</small></span></li>
			</ol>
		</aside>

		<section class="signup-form-panel" aria-label="Create an account">
			<main id="signup-main" class="signup-form">{@render children()}</main>
			<p class="workspace-note">Your next experiment starts here.</p>
		</section>
	</div>

	<footer class="signup-footer">
		<a href="/" class="home-link"><ArrowLeft aria-hidden="true" /> Back to home</a>
		<span class="footer-edition">LLM Lab <span aria-hidden="true">/</span> Evaluation research</span>
		<a href="/auth/login" class="login-link">Already a member? <span>Log in <ArrowUpRight aria-hidden="true" /></span></a>
	</footer>
</div>

<style>
	.signup-shell { min-height: 100dvh; display: flex; flex-direction: column; background: var(--background); color: var(--foreground); }
	.signup-topbar, .signup-footer { width: 100%; max-width: 1600px; margin-inline: auto; padding-inline: clamp(24px, 5vw, 80px); display: flex; align-items: center; justify-content: space-between; gap: 24px; }
	.signup-topbar { min-height: 104px; }
	.brand-link { border-radius: 6px; }
	.topbar-actions { display: flex; align-items: center; gap: 24px; }
	.research-label { color: var(--muted-foreground); font-size: 13px; }
	.signup-content { --story-gap: clamp(48px, 6vw, 96px); --story-gutter: max(clamp(24px, 5vw, 80px), calc((100vw - 1440px) / 2 + clamp(24px, 5vw, 80px))); width: 100%; max-width: 1440px; margin: auto; padding: 32px clamp(24px, 5vw, 80px) 48px; display: grid; grid-template-columns: minmax(0, 1.15fr) minmax(0, 1fr); gap: clamp(48px, 6vw, 96px); align-items: center; }
	.signup-story { position: relative; isolation: isolate; min-width: 0; overflow: hidden; align-self: stretch; display: flex; flex-direction: column; justify-content: center; padding-block: 32px; margin-left: calc(-1 * var(--story-gutter)); padding-left: var(--story-gutter); margin-right: calc(-1 * var(--story-gap)); padding-right: var(--story-gap); }
	.signup-story :global(.story-field) { z-index: -1; opacity: .5; }
	.eyebrow { display: flex; align-items: center; gap: 10px; color: var(--muted-foreground); font: 11px var(--font-mono); letter-spacing: .12em; text-transform: uppercase; }
	.edition-mark { width: 7px; height: 7px; background: var(--accent-3); border-radius: 50%; }
	h2 { margin-top: 24px; font: 500 clamp(40px, 4.4vw, 64px)/1.08 var(--font-display); letter-spacing: -.045em; text-wrap: balance; }
	h2 em { color: var(--primary); font-weight: 500; }
	.story-description { max-width: 390px; margin-top: 24px; color: var(--muted-foreground); font-size: 16px; line-height: 1.75; }
	.field-panel { position: relative; isolation: isolate; height: 192px; margin-top: 32px; overflow: hidden; border-block: 1px solid var(--border); }
	.field-caption { position: absolute; inset: 14px 0 auto; display: flex; justify-content: space-between; color: var(--muted-foreground); font: 10px var(--font-mono); letter-spacing: .13em; pointer-events: none; }
	.field-symbol { position: absolute; left: 24px; top: 53px; color: var(--primary); background: var(--background); padding: 0 10px; font: 48px var(--font-mono); letter-spacing: -.12em; pointer-events: none; }
	.field-symbol span { color: var(--accent-2); }
	.field-note { position: absolute; bottom: 15px; left: 0; background: var(--background); padding-right: 12px; color: var(--muted-foreground); font: 11px var(--font-mono); pointer-events: none; }
	.story-steps { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); margin-top: 24px; gap: 16px; padding: 0; list-style: none; }
	.story-steps li { position: relative; display: flex; gap: 10px; align-items: flex-start; padding-top: 22px; }
	.step-number { position: absolute; top: 0; font: 10px var(--font-mono); color: var(--muted-foreground); }
	.story-steps :global(svg) { width: 16px; height: 16px; margin-top: 3px; color: var(--primary); flex-shrink: 0; }
	.story-steps strong { display: block; font-size: 13px; font-weight: 500; }
	.story-steps small { display: block; margin-top: 4px; font-size: 11px; color: var(--muted-foreground); }
	.signup-form-panel { padding: 36px 0 32px clamp(24px, 4vw, 64px); border-left: 1px solid var(--border); }
	.signup-form { max-width: 400px; margin-inline: auto; }
	.workspace-note { max-width: 400px; margin: 28px auto 0; color: var(--muted-foreground); font-size: 12px; text-align: center; }
	.signup-footer { min-height: 80px; border-top: 1px solid var(--border); color: var(--muted-foreground); font-size: 12px; }
	.home-link, .login-link, .login-link span { display: inline-flex; align-items: center; gap: 8px; }
	.signup-footer a { min-height: 44px; transition: color 150ms; }
	.signup-footer a:hover { color: var(--primary); }
	.signup-footer :global(svg) { width: 14px; height: 14px; }
	.footer-edition { font-family: var(--font-mono); font-size: 10px; letter-spacing: .06em; }
	.footer-edition span { margin-inline: 12px; color: var(--primary); }
	.login-link span { color: var(--foreground); }
	a:focus-visible { outline: 2px solid var(--ring); outline-offset: 5px; }
	@media (min-width: 1600px) { .signup-content { padding-block: 64px; } }
	@media (max-width: 1023px) {
		.signup-topbar { min-height: 88px; }
		.signup-content { display: block; max-width: 540px; padding-block: 24px 48px; }
		.signup-story { display: none; }
		.signup-form-panel { padding: 0; border: 0; }
		.research-label, .login-link { display: none; }
		.signup-footer { min-height: 72px; }
	}
	@media (max-width: 479px) {
		.signup-topbar { min-height: 84px; }
		.signup-content { padding-top: 16px; }
		.footer-edition { display: none; }
	}
</style>
