/**
 * Lazy Mermaid integration. Renders all `.mermaid-placeholder` elements
 * inside the given container, theming based on the current app theme.
 */

import type { MermaidConfig } from 'mermaid';

let _mermaidPromise: Promise<typeof import('mermaid').default> | null = null;

async function getMermaid() {
	if (!_mermaidPromise) {
		_mermaidPromise = import('mermaid').then((m) => m.default);
	}
	return _mermaidPromise;
}

function configFor(theme: 'light' | 'dark'): MermaidConfig {
	return {
		startOnLoad: false,
		securityLevel: 'strict',
		theme: theme === 'dark' ? 'dark' : 'default',
		themeVariables: {
			fontFamily: 'ui-sans-serif, system-ui, sans-serif',
			fontSize: '14px',
		},
		flowchart: { htmlLabels: true, curve: 'basis' },
	};
}

let _runCounter = 0;

export async function renderMermaidIn(container: HTMLElement, theme: 'light' | 'dark') {
	const placeholders = container.querySelectorAll<HTMLElement>('.mermaid-placeholder');
	if (placeholders.length === 0) return;

	const mermaid = await getMermaid();
	mermaid.initialize(configFor(theme));

	for (const el of Array.from(placeholders)) {
		const source = el.dataset.source ?? '';
		if (!source.trim()) continue;
		const id = `mermaid-${++_runCounter}-${Math.random().toString(36).slice(2, 8)}`;
		try {
			const { svg, bindFunctions } = await mermaid.render(id, source);
			const wrapper = document.createElement('div');
			wrapper.className = 'mermaid-rendered';
			wrapper.innerHTML = svg;
			el.replaceWith(wrapper);
			bindFunctions?.(wrapper);
		} catch (err) {
			const errBox = document.createElement('pre');
			errBox.className = 'mermaid-error';
			errBox.textContent = `Mermaid render error: ${(err as Error).message}\n\n${source}`;
			el.replaceWith(errBox);
		}
	}
}
