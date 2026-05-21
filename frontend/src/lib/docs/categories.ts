import Rocket from '@lucide/svelte/icons/rocket';
import LayoutTemplate from '@lucide/svelte/icons/layout-template';
import BookOpen from '@lucide/svelte/icons/book-open';
import Wrench from '@lucide/svelte/icons/wrench';
import FolderOpen from '@lucide/svelte/icons/folder-open';
import type { Component } from 'svelte';

export interface CategoryMeta {
	label: string;
	icon: Component;
	tagline: string;
	accent: string;
}

export const CATEGORY_ORDER: string[] = [
	'Getting Started',
	'Architecture',
	'Reference',
	'Operations',
	'Other',
];

export const CATEGORY_META: Record<string, CategoryMeta> = {
	'Getting Started': {
		label: 'Getting Started',
		icon: Rocket,
		tagline: 'Install, set up, and ship your first run.',
		accent: 'text-emerald-500',
	},
	Architecture: {
		label: 'Architecture',
		icon: LayoutTemplate,
		tagline: 'System design, pipelines, and how everything fits together.',
		accent: 'text-sky-500',
	},
	Reference: {
		label: 'Reference',
		icon: BookOpen,
		tagline: 'API, analyzers, models, and template specifications.',
		accent: 'text-violet-500',
	},
	Operations: {
		label: 'Operations',
		icon: Wrench,
		tagline: 'Deploy, monitor, and troubleshoot in production.',
		accent: 'text-amber-500',
	},
	Other: {
		label: 'Other',
		icon: FolderOpen,
		tagline: 'Everything else \u2014 internal notes, todos, and archives.',
		accent: 'text-muted-foreground',
	},
};

export function metaFor(category: string | undefined): CategoryMeta {
	return CATEGORY_META[category ?? 'Other'] ?? CATEGORY_META.Other;
}
