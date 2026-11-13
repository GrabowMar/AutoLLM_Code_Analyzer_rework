/**
 * Library components and helpers (WP-14), shared with the Study builder (WP-15) and the sample/slot/
 * pattern screens (WP-16). Props are documented at the top of each component; the contract is announced
 * in docs/design/SAMPLE_GEN_V3_CHANGES.md ("WP-14 decisions").
 */
export { default as PinnedRefBadge } from "./PinnedRefBadge.svelte";
export { default as LibraryRefPicker } from "./LibraryRefPicker.svelte";
export { default as ResourcePolicyEditor } from "./ResourcePolicyEditor.svelte";
export { default as KnobField } from "./KnobField.svelte";
export { default as PromptTemplateEditor } from "./PromptTemplateEditor.svelte";
export { default as PromptSetEditor } from "./PromptSetEditor.svelte";
export { default as DetectorEditor } from "./DetectorEditor.svelte";
export { default as TaskEditor } from "./TaskEditor.svelte";
export { default as StackProfileEditor } from "./StackProfileEditor.svelte";
export { default as StarterViewer } from "./StarterViewer.svelte";
export { default as StyleEditor } from "./StyleEditor.svelte";
export { default as GuidanceEditor } from "./GuidanceEditor.svelte";
export { default as HarnessPresetEditor } from "./HarnessPresetEditor.svelte";
export { default as SamplingPresetEditor } from "./SamplingPresetEditor.svelte";
export { default as EvalSuiteViewer } from "./EvalSuiteViewer.svelte";
export { default as ContentEditor } from "./ContentEditor.svelte";
export { default as JsonContentEditor } from "./JsonContentEditor.svelte";
export { default as StimulusText } from "./StimulusText.svelte";
export { default as RenderContextPicker } from "./RenderContextPicker.svelte";
export { default as PromptPlayground } from "./PromptPlayground.svelte";
export { default as VersionDiff } from "./VersionDiff.svelte";
export { default as VersionHistory } from "./VersionHistory.svelte";
export { default as LibraryNav } from "./LibraryNav.svelte";
export { default as LibraryList } from "./LibraryList.svelte";
export { default as PackagesDialog } from "./PackagesDialog.svelte";
export { default as NumberInput } from "./NumberInput.svelte";
export { default as ListInput } from "./ListInput.svelte";

export {
  libraryStore,
  invalidateLibrary,
  KindList,
  type AskConfirm,
  type ConfirmRequest,
  type LibraryMe,
} from "./library.svelte";
export {
  KIND_INFO,
  KIND_GROUPS,
  ALL_KINDS,
  kindInfo,
  libraryHref,
  canEdit,
  itemSubline,
  type KindInfo,
} from "./kinds";
export {
  KEY_LIMITS_GROUP,
  POLICY_GROUPS,
  affectsTreatment,
  baseTier,
  classBadge,
  closestTier,
  fingerprintChanges,
  formatKnobValue,
  getKnob,
  groupKnobs,
  groupOf,
  jsonEqual,
  knobClass,
  knobLabel,
  knobState,
  overrides,
  policyHints,
  resetKnob,
  setKnob,
  summaryLine,
  tierLabel,
  type BudgetBasis,
  type ClassBadge,
  type KnobState,
} from "./policy";
export {
  segmentStimulus,
  stimulusOwners,
  ownerHref,
  ownerSlot,
  OWNER_LABEL,
  type StimulusSegment,
} from "./spans";
export {
  parseUnified,
  lineDiff,
  collapseContext,
  structuredRows,
  diffStats,
  type DiffRow,
} from "./diff";
export {
  completionList,
  completionsAt,
  usedVariables,
  variableIssues,
  TEMPLATE_FILTERS,
} from "./variables";
export {
  draftFromTask,
  taskFromDraft,
  localTaskIssues,
  emptyTaskContent,
  type TaskDraft,
} from "./task";
export { defaultAppContext, defaultPromptSet } from "./preview";
export { slugify, isValidSlug } from "./slugify";
