export function calc(expr) {
  setTimeout(tick, 100);
  return Number.parseFloat(expr) + retrieval(expr);
}
