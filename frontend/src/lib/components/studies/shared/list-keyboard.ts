/**
 * Arrow-key navigation for long pick lists (ModelPicker, AppSpecPicker): mark
 * the scroll container `data-nav-list`, give every focusable row
 * `data-nav-item` and `onkeydown={navigateList}`. Tab still enters/leaves the
 * list as usual.
 */
export function navigateList(event: KeyboardEvent): void {
  if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
  const row = event.currentTarget as HTMLElement | null;
  const container = row?.closest<HTMLElement>("[data-nav-list]");
  if (!container) return;
  const items = Array.from(
    container.querySelectorAll<HTMLElement>("[data-nav-item]"),
  ).filter((el) => !el.hasAttribute("disabled"));
  if (items.length === 0) return;

  const current = items.indexOf(document.activeElement as HTMLElement);
  let next: number;
  if (event.key === "Home") next = 0;
  else if (event.key === "End") next = items.length - 1;
  else if (event.key === "ArrowDown")
    next = current < 0 ? 0 : Math.min(items.length - 1, current + 1);
  else next = current < 0 ? items.length - 1 : Math.max(0, current - 1);

  event.preventDefault();
  items[next]?.focus();
  items[next]?.scrollIntoView({ block: "nearest" });
}
