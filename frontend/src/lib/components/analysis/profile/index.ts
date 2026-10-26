// Analysis profile building blocks (profiles pages, study wizard, ad-hoc runs).
export { default as ProfileEditor } from "./ProfileEditor.svelte";
export { default as ReviewerConfigEditor } from "./ReviewerConfigEditor.svelte";
export { default as ToolConfigEditor } from "./ToolConfigEditor.svelte";
export { default as ToolsSection } from "./ToolsSection.svelte";
export { default as ScopeEditor } from "./ScopeEditor.svelte";
export { default as OutcomesEditor } from "./OutcomesEditor.svelte";
export { default as ReviewerModelField } from "./ReviewerModelField.svelte";
export { default as ProfileCloneDialog } from "./ProfileCloneDialog.svelte";
export { default as ProfileVersionList } from "./ProfileVersionList.svelte";

export type {
  ProfileEditorSection,
  ProfileValidationState,
} from "./ProfileEditor.svelte";
export type {
  ReviewerModelInfo,
  ReviewerModelReasoning,
} from "./ReviewerModelField.svelte";

export {
  loadProfileDefaults,
  reviewerOptionsError,
  reviewerOptionsOf,
} from "./defaults";
export {
  ENGINE_LABELS,
  KIND_INFO,
  ORIGIN_INFO,
  SEVERITY_LABELS,
  TOOL_CATEGORY_INFO,
  TOOL_KIND_LABELS,
  categoryLabel,
  cloneJson,
  describeOption,
  modifiedOptions,
  normalizeConfigErrors,
  requiredCount,
  reviewerSummary,
  sameValue,
  shortHash,
  shortModel,
  stableStringify,
  toolIcon,
} from "./profile-utils";
