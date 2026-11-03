/**
 * Target validation display (spec 7.4) and the prefilled slot-study link (spec 10.5 step 3).
 *
 * A target is usable in a study only when validation is `ok` or `uninformative` (the backend's
 * `target_usable`, mirrored in `SlotTargetOut.usable`); `pending` and `error` targets are never passed on.
 */
import type { Tone } from "$lib/constants/colors";
import type { SlotTargetOut } from "$lib/api/v3/types";

export const VALIDATION_TONES: Partial<Record<string, Tone>> = {
  pending: "info",
  ok: "success",
  uninformative: "warning",
  error: "danger",
};

export function validationLabel(
  target: Pick<SlotTargetOut, "validation">,
): string {
  const v = target.validation;
  switch (v?.status) {
    case "ok":
      return "validated";
    case "uninformative":
      return "uninformative";
    case "error":
      return "validation error";
    default:
      return "validating";
  }
}

/** `/studies/new?mode=slot&slot_sets=a,b&targets=x,y` with only usable targets (and the sets they belong to). */
export function slotStudyHref(
  setIds: string[],
  targets: Pick<SlotTargetOut, "id" | "slot_set" | "usable">[],
): string {
  const usable = targets.filter((t) => t.usable);
  const sets = [
    ...new Set([
      ...setIds.filter((id) => usable.some((t) => t.slot_set === id)),
      ...usable.map((t) => t.slot_set),
    ]),
  ];
  const params = new URLSearchParams({ mode: "slot" });
  if (sets.length) params.set("slot_sets", sets.join(","));
  if (usable.length) params.set("targets", usable.map((t) => t.id).join(","));
  return `/studies/new?${params.toString().replace(/%2C/g, ",")}`;
}
