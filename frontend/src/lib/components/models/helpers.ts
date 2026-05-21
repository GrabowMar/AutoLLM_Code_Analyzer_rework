import type { LLMModelDetail } from '$lib/api/client';
import { toast } from 'svelte-sonner';
import MessageSquare from '@lucide/svelte/icons/message-square';
import Eye from '@lucide/svelte/icons/eye';
import Wrench from '@lucide/svelte/icons/wrench';
import Radio from '@lucide/svelte/icons/radio';
import Braces from '@lucide/svelte/icons/braces';
import Mic from '@lucide/svelte/icons/mic';
import Box from '@lucide/svelte/icons/box';
import Zap from '@lucide/svelte/icons/zap';

export interface SkillBadge {
	name: string;
	icon: typeof Zap;
	active: boolean;
}

export interface PricingTier {
	label: string;
	perMillionTokens: number;
}

export function formatPrice(price: number): string {
	if (price === 0) return 'Free';
	if (price < 0.01) return `$${price.toFixed(4)}`;
	return `$${price.toFixed(2)}`;
}

export function formatTokens(n: number): string {
	if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
	if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
	return String(n);
}

export function formatNumber(n: number): string {
	return n.toLocaleString();
}

export function costEfficiencyGrade(score: number): { grade: string; color: string } {
	if (score >= 0.9) return { grade: 'A+', color: 'text-emerald-500' };
	if (score >= 0.8) return { grade: 'A', color: 'text-emerald-500' };
	if (score >= 0.7) return { grade: 'A-', color: 'text-emerald-400' };
	if (score >= 0.6) return { grade: 'B+', color: 'text-blue-500' };
	if (score >= 0.5) return { grade: 'B', color: 'text-blue-500' };
	if (score >= 0.4) return { grade: 'B-', color: 'text-blue-400' };
	if (score >= 0.3) return { grade: 'C+', color: 'text-amber-500' };
	if (score >= 0.2) return { grade: 'C', color: 'text-amber-500' };
	return { grade: 'D', color: 'text-red-500' };
}

export function getMeta(m: LLMModelDetail): Record<string, unknown> {
	return (m.metadata || {}) as Record<string, unknown>;
}

export function getCaps(m: LLMModelDetail): Record<string, unknown> {
	return (m.capabilities_json || {}) as Record<string, unknown>;
}

export function getArchitecture(m: LLMModelDetail): Record<string, unknown> {
	const caps = getCaps(m);
	return (caps.architecture || {}) as Record<string, unknown>;
}

export function getHuggingFaceId(m: LLMModelDetail): string | null {
	const caps = getCaps(m);
	const hfId = caps.hugging_face_id as string | undefined;
	return hfId || null;
}

export function getSupportedParameters(m: LLMModelDetail): string[] {
	const caps = getCaps(m);
	const meta = getMeta(m);
	const params = (caps.supported_parameters as string[]) || (meta.openrouter_supported_parameters as string[]) || [];
	return params;
}

export function getPerRequestLimits(m: LLMModelDetail): Record<string, number> {
	const caps = getCaps(m);
	return (caps.per_request_limits || {}) as Record<string, number>;
}

export function getDefaultParameters(m: LLMModelDetail): Record<string, unknown> {
	const caps = getCaps(m);
	return (caps.default_parameters || {}) as Record<string, unknown>;
}

export function getCoreSkills(m: LLMModelDetail): SkillBadge[] {
	const caps = getCaps(m);
	const arch = getArchitecture(m);
	const inputMods = (arch.input_modalities || []) as string[];
	const outputMods = (arch.output_modalities || []) as string[];
	return [
		{ name: 'Text/Chat', icon: MessageSquare, active: true },
		{ name: 'Vision', icon: Eye, active: m.supports_vision || inputMods.includes('image') },
		{ name: 'Functions', icon: Wrench, active: m.supports_function_calling },
		{ name: 'Streaming', icon: Radio, active: m.supports_streaming },
		{ name: 'JSON Mode', icon: Braces, active: m.supports_json_mode },
		{ name: 'Audio', icon: Mic, active: inputMods.includes('audio') || outputMods.includes('audio') },
		{ name: 'Multimodal', icon: Box, active: inputMods.length > 1 || (caps.multimodal as boolean) || false },
	];
}

export function getCapabilityMatrix(m: LLMModelDetail): Record<string, boolean> {
	const caps = getCaps(m);
	const result: Record<string, boolean> = {};
	for (const [key, value] of Object.entries(caps)) {
		if (typeof value === 'boolean') {
			result[key] = value;
		}
	}
	result['function_calling'] = m.supports_function_calling;
	result['vision'] = m.supports_vision;
	result['streaming'] = m.supports_streaming;
	result['json_mode'] = m.supports_json_mode;
	return result;
}

export function getAllPricingTiers(m: LLMModelDetail): PricingTier[] {
	const meta = getMeta(m);
	const caps = getCaps(m);
	const pricing = (meta.openrouter_pricing || (caps.pricing as Record<string, string>) || {}) as Record<string, string>;
	const tiers: PricingTier[] = [];
	const tierMap: Record<string, string> = {
		input_cache_read: 'Cache Read',
		input_cache_write: 'Cache Write',
		web_search: 'Web Search',
		internal_reasoning: 'Internal Reasoning',
		request: 'Per Request',
		image: 'Per Image',
	};
	for (const [key, label] of Object.entries(tierMap)) {
		const raw = pricing[key];
		if (raw) {
			const val = parseFloat(raw);
			if (val > 0) tiers.push({ label, perMillionTokens: val * 1_000_000 });
		}
	}
	return tiers;
}

export function formatMetaValue(value: unknown): string {
	if (value === null || value === undefined) return '—';
	if (typeof value === 'object') {
		if (Array.isArray(value)) {
			if (value.length === 0) return '—';
			if (value.length > 3) return `${value.slice(0, 3).join(', ')} (+${value.length - 3})`;
			return value.join(', ');
		}
		return `${Object.keys(value).length} keys`;
	}
	if (typeof value === 'number') {
		return value > 1000 ? formatNumber(value) : String(value);
	}
	const sv = String(value);
	return sv.length > 80 ? sv.slice(0, 77) + '...' : sv;
}

export async function copyToClipboard(text: string): Promise<void> {
	await navigator.clipboard.writeText(text);
	toast.success('Copied to clipboard');
}

export function exportJson(m: LLMModelDetail): void {
	const data = { model: m, capabilities_json: m.capabilities_json, metadata: m.metadata };
	const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
	const url = URL.createObjectURL(blob);
	const a = document.createElement('a');
	a.href = url;
	a.download = `${m.canonical_slug}.json`;
	a.click();
	URL.revokeObjectURL(url);
}

export function stripMarkdown(text: string): string {
	return text
		.replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
		.replace(/[*_]{1,2}([^*_]+)[*_]{1,2}/g, '$1')
		.replace(/#{1,6}\s/g, '')
		.replace(/`([^`]+)`/g, '$1');
}
