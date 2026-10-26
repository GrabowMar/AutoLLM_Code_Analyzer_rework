/**
 * Attachment for a horizontal scroller (`{@attach scrollHint()}`): sets
 * `data-overflow-end` (and with `edges: "both"`, `data-overflow-start`) while
 * there is more content off that edge, so CSS can fade the edge as a cue
 * (`.scroll-hint` in app.css). A scroller with no focusable stop of its own
 * gets `tabindex="0"` while it overflows, so keyboard users can scroll it.
 * With `follow`, the matching descendant (e.g. the active tab) is scrolled
 * into view whenever its attributes change.
 */
export function scrollHint({
  edges = "end",
  focusable = true,
  follow,
}: { edges?: "end" | "both"; focusable?: boolean; follow?: string } = {}) {
  return (node: HTMLElement) => {
    const ownTabindex = node.hasAttribute("tabindex");
    const update = () => {
      const max = node.scrollWidth - node.clientWidth;
      const overflows = max > 1;
      node.toggleAttribute(
        "data-overflow-end",
        overflows && node.scrollLeft < max - 1,
      );
      node.toggleAttribute(
        "data-overflow-start",
        edges === "both" && overflows && node.scrollLeft > 1,
      );
      if (focusable && !ownTabindex) {
        if (overflows) node.setAttribute("tabindex", "0");
        else node.removeAttribute("tabindex");
      }
    };
    const reveal = () => {
      const target = follow ? node.querySelector<HTMLElement>(follow) : null;
      if (!target) return;
      const box = node.getBoundingClientRect();
      const r = target.getBoundingClientRect();
      if (r.left < box.left) node.scrollLeft -= box.left - r.left + 16;
      else if (r.right > box.right) node.scrollLeft += r.right - box.right + 16;
    };
    const resize = new ResizeObserver(update);
    resize.observe(node);
    for (const child of node.children) resize.observe(child);
    const mutations = follow
      ? new MutationObserver(() => {
          reveal();
          update();
        })
      : null;
    mutations?.observe(node, {
      subtree: true,
      attributes: true,
      attributeFilter: ["data-state"],
    });
    node.addEventListener("scroll", update, { passive: true });
    reveal();
    update();
    return () => {
      resize.disconnect();
      mutations?.disconnect();
      node.removeEventListener("scroll", update);
    };
  };
}
