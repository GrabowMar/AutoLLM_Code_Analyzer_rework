/**
 * Client-side Markdown rendering pipeline.
 *
 * Code blocks support the following info-string flags:
 *
 *   ```ts title="src/foo.ts" {1,3-5} showLineNumbers
 *   ```
 *
 *   - `title="..."`  — filename / caption shown in the header
 *   - `{1,3-5}`      — highlight ranges (1-based line numbers)
 *   - `showLineNumbers` (or just `numbers`) — render a gutter
 *   - `wrap`         — soft-wrap long lines
 *
 * Mermaid code fences become a `<div class="mermaid-placeholder">` that
 * `lib/docs/mermaid.ts` upgrades after mount.
 */

import { Marked } from 'marked';
import markedAlert from 'marked-alert';
import { gfmHeadingId } from 'marked-gfm-heading-id';
import DOMPurify from 'dompurify';
import { createHighlighter, type Highlighter } from 'shiki';
import {
	transformerNotationDiff,
	transformerNotationHighlight,
	transformerNotationFocus,
} from '@shikijs/transformers';

export interface Heading {
	id: string;
	text: string;
	level: number;
}

export interface RenderedDoc {
	html: string;
	headings: Heading[];
	hasMermaid: boolean;
}

const SHIKI_LANGS = [
	'bash',
	'sh',
	'shell',
	'console',
	'python',
	'py',
	'javascript',
	'js',
	'typescript',
	'ts',
	'tsx',
	'jsx',
	'json',
	'jsonc',
	'yaml',
	'yml',
	'toml',
	'ini',
	'dockerfile',
	'docker',
	'html',
	'css',
	'scss',
	'svelte',
	'sql',
	'diff',
	'markdown',
	'md',
	'text',
	'plaintext',
];

const LANG_ALIASES: Record<string, string> = {
	py: 'python',
	js: 'javascript',
	ts: 'typescript',
	yml: 'yaml',
	docker: 'dockerfile',
	sh: 'bash',
	shell: 'bash',
	console: 'bash',
	plaintext: 'text',
	md: 'markdown',
};

let _highlighterPromise: Promise<Highlighter> | null = null;

function getHighlighter(): Promise<Highlighter> {
	if (!_highlighterPromise) {
		_highlighterPromise = createHighlighter({
			themes: ['github-light', 'github-dark'],
			langs: SHIKI_LANGS,
		});
	}
	return _highlighterPromise;
}

function escapeHtml(s: string): string {
	return s
		.replace(/&/g, '&amp;')
		.replace(/</g, '&lt;')
		.replace(/>/g, '&gt;')
		.replace(/"/g, '&quot;')
		.replace(/'/g, '&#39;');
}

function escapeAttr(s: string): string {
	return s.replace(/&/g, '&amp;').replace(/"/g, '&quot;');
}

function langOrFallback(lang: string, highlighter: Highlighter): string {
	const aliased = LANG_ALIASES[lang] ?? lang;
	const loaded = new Set<string>(highlighter.getLoadedLanguages());
	if (loaded.has(aliased)) return aliased;
	return 'text';
}

interface CodeMeta {
	lang: string;
	title?: string;
	highlightLines: Set<number>;
	showLineNumbers: boolean;
	wrap: boolean;
}

function parseCodeMeta(rawLang: string): CodeMeta {
	const info = (rawLang || '').trim();
	// First token is the language.
	const firstSpace = info.search(/\s/);
	const lang = (firstSpace === -1 ? info : info.slice(0, firstSpace)).toLowerCase();
	const rest = firstSpace === -1 ? '' : info.slice(firstSpace + 1);

	const meta: CodeMeta = {
		lang,
		highlightLines: new Set<number>(),
		showLineNumbers: false,
		wrap: false,
	};

	// title="..."
	const titleMatch = rest.match(/title=(?:"([^"]+)"|'([^']+)'|(\S+))/);
	if (titleMatch) meta.title = titleMatch[1] ?? titleMatch[2] ?? titleMatch[3];

	// {1,3-5}
	const hlMatch = rest.match(/\{([\d,\s\-]+)\}/);
	if (hlMatch) {
		for (const part of hlMatch[1].split(',')) {
			const trimmed = part.trim();
			if (!trimmed) continue;
			if (trimmed.includes('-')) {
				const [a, b] = trimmed.split('-').map((n) => parseInt(n, 10));
				if (Number.isFinite(a) && Number.isFinite(b)) {
					for (let i = Math.min(a, b); i <= Math.max(a, b); i++) meta.highlightLines.add(i);
				}
			} else {
				const n = parseInt(trimmed, 10);
				if (Number.isFinite(n)) meta.highlightLines.add(n);
			}
		}
	}

	if (/\b(showLineNumbers|numbers)\b/i.test(rest)) meta.showLineNumbers = true;
	if (/\bwrap\b/i.test(rest)) meta.wrap = true;

	return meta;
}

/** Render markdown to sanitized HTML. */
export async function renderMarkdown(raw: string): Promise<RenderedDoc> {
	const highlighter = await getHighlighter();

	const marked = new Marked({
		gfm: true,
		breaks: false,
	});

	marked.use(markedAlert());
	marked.use(gfmHeadingId());

	marked.use({
		renderer: {
			code({ text, lang }: { text: string; lang?: string }) {
				const meta = parseCodeMeta(lang ?? '');
				if (meta.lang === 'mermaid') {
					return `<div class="mermaid-placeholder" data-source="${escapeAttr(text)}"></div>`;
				}

				const effective = langOrFallback(meta.lang || 'text', highlighter);
				const displayLang = meta.lang || 'text';

				const highlighted = highlighter.codeToHtml(text, {
					lang: effective,
					themes: { light: 'github-light', dark: 'github-dark' },
					defaultColor: false,
					transformers: [
						transformerNotationDiff({ matchAlgorithm: 'v3' }),
						transformerNotationHighlight({ matchAlgorithm: 'v3' }),
						transformerNotationFocus({ matchAlgorithm: 'v3' }),
						{
							name: 'docs:lines',
							line(node, line) {
								node.properties['data-line'] = line;
								if (meta.highlightLines.has(line)) {
									const cur = (node.properties.class as string | undefined) ?? '';
									node.properties.class = `${cur} highlighted`.trim();
								}
							},
							pre(node) {
								const cur = (node.properties.class as string | undefined) ?? '';
								const flags = [
									meta.showLineNumbers ? 'has-line-numbers' : '',
									meta.wrap ? 'is-wrapped' : '',
								]
									.filter(Boolean)
									.join(' ');
								if (flags) node.properties.class = `${cur} ${flags}`.trim();
							},
						},
					],
				});

				const titleAttr = meta.title ? ` data-title="${escapeAttr(meta.title)}"` : '';
				const titleNode = meta.title
					? `<span class="code-block__title">${escapeHtml(meta.title)}</span>`
					: '';

				return `<figure class="code-block" data-lang="${escapeAttr(displayLang)}"${titleAttr}>
<figcaption class="code-block__header">
<span class="code-block__dots" aria-hidden="true"><i></i><i></i><i></i></span>
${titleNode}
<span class="code-block__lang">${escapeHtml(displayLang)}</span>
<button type="button" class="code-block__copy" data-copy="${escapeAttr(text)}" aria-label="Copy code">
<svg class="code-block__icon code-block__icon--copy" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path fill="currentColor" d="M4 4V2a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2h-2v2a2 2 0 0 1-2 2H2a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zm2 0h4a2 2 0 0 1 2 2v4h2V2H6zM2 6v8h8V6z"/></svg>
<svg class="code-block__icon code-block__icon--check" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path fill="currentColor" d="M13.78 4.22a.75.75 0 0 1 0 1.06l-7.25 7.25a.75.75 0 0 1-1.06 0L2.22 9.28a.75.75 0 1 1 1.06-1.06L6 10.94l6.72-6.72a.75.75 0 0 1 1.06 0z"/></svg>
<span class="code-block__copy-label">Copy</span>
</button>
</figcaption>
<div class="code-block__body">${highlighted}</div>
</figure>`;
			},
		},
	});

	const dirty = await marked.parse(raw, { async: true });
	const clean = DOMPurify.sanitize(dirty, {
		ADD_TAGS: ['figure', 'figcaption', 'svg', 'path', 'i'],
		ADD_ATTR: [
			'data-lang',
			'data-source',
			'data-copy',
			'data-theme',
			'data-line',
			'data-title',
			'data-highlighted',
			'data-focused',
			'data-diff',
			'style',
			'tabindex',
			'viewBox',
			'width',
			'height',
			'fill',
			'aria-hidden',
			'aria-label',
		],
	});

	const headings: Heading[] = [];
	let hasMermaid = false;
	if (typeof window !== 'undefined') {
		const tmpl = document.createElement('template');
		tmpl.innerHTML = clean;
		const root = tmpl.content;
		root.querySelectorAll('h1, h2, h3, h4').forEach((el) => {
			const id = el.getAttribute('id');
			if (!id) return;
			headings.push({
				id,
				text: (el.textContent ?? '').trim(),
				level: Number(el.tagName.slice(1)),
			});
		});
		hasMermaid = root.querySelectorAll('.mermaid-placeholder').length > 0;
	}

	return { html: clean, headings, hasMermaid };
}
