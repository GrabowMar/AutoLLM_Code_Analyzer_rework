import { goto } from "$app/navigation";
import { scrollHint } from "$lib/utils/scroll-hint";

export { default as SortableTh } from "./SortableTh.svelte";
export { default as TableToolbar } from "./TableToolbar.svelte";
export { default as ScoreCell } from "./ScoreCell.svelte";

/**
 * Attachment for `div.table-frame` (`{@attach tableFrame}`). While the table fits
 * the frame it sets `data-fits`: the page does the scrolling and the header sticks
 * under the app bar. A wider table keeps the frame as its own bounded scroll box so
 * the horizontal scrollbar stays on screen. It also gets `scrollHint()`: an edge
 * fade on phones and a tab stop while it scrolls sideways.
 */
export function tableFrame(node: HTMLElement) {
  const detachHint = scrollHint()(node);
  const update = () => {
    const table = node.querySelector("table");
    node.toggleAttribute(
      "data-fits",
      !table || table.offsetWidth <= node.clientWidth + 1,
    );
  };
  const observer = new ResizeObserver(update);
  observer.observe(node);
  const table = node.querySelector("table");
  if (table) observer.observe(table);
  update();
  return () => {
    observer.disconnect();
    detachHint();
  };
}

const INTERACTIVE =
  'a, button, input, label, select, textarea, summary, [role="checkbox"], [role="button"], [role="menuitem"]';

/**
 * Click handler for a `<tr class="row-link">`: opens `href` unless the click
 * landed on a control inside the row or the user is selecting text.
 * Ctrl/⌘-click opens a new tab. Keep a real `<a>` in the row for keyboard users.
 */
export function rowLink(href: string) {
  return (e: MouseEvent) => {
    const target = e.target as HTMLElement | null;
    if (target?.closest(INTERACTIVE)) return;
    if (window.getSelection()?.toString()) return;
    if (e.metaKey || e.ctrlKey || e.button === 1) {
      window.open(href, "_blank", "noopener");
      return;
    }
    goto(href);
  };
}

/**
 * OpenRouter names repeat the provider ("Amazon: Nova 2 Lite"); tables that
 * already show the provider render the short name and keep the full one as a tooltip.
 */
export function shortModelName(name: string | null | undefined): string {
  if (!name) return "";
  const i = name.indexOf(": ");
  return i > 0 && i <= 40 ? name.slice(i + 2) : name;
}

export function modelNamePrefix(name: string | null | undefined): string {
  if (!name) return "";
  const i = name.indexOf(": ");
  return i > 0 && i <= 40 ? name.slice(0, i) : "";
}
