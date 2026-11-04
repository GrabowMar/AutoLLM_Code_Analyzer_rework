/**
 * Sample render contexts for prompt previews: a sensible default cell from what the library holds
 * (the `crud_todo_list` task and `app-default` prompt set when present, a style compatible with the
 * stack's frontend), so a preview renders without the user picking eight levels first.
 */
import type {
  AppContextFrom,
  LibraryItemOut,
  PromptMode,
  StackProfileContent,
  StyleContent,
} from "$lib/api/v3/types";

function prefer<T extends { slug: string }>(
  items: T[],
  ...slugs: string[]
): T | undefined {
  for (const slug of slugs) {
    const hit = items.find((i) => i.slug === slug);
    if (hit) return hit;
  }
  return items[0];
}

export interface PreviewLists {
  tasks: LibraryItemOut<"task">[];
  stacks: LibraryItemOut<"stack">[];
  styles: LibraryItemOut<"style">[];
  guidance: LibraryItemOut<"guidance">[];
  harnessPresets: LibraryItemOut<"harness_preset">[];
}

export function defaultAppContext(lists: PreviewLists): AppContextFrom | null {
  const task = prefer(lists.tasks, "crud_todo_list");
  if (!task) return null;
  const stack =
    prefer(
      lists.stacks.filter(
        (s) => (s.content as StackProfileContent).choice_mode === "fixed",
      ),
      "flask-react",
      "fastapi-react",
    ) ?? lists.stacks[0];
  const frontend = stack
    ? (stack.content as StackProfileContent).frontend.framework
    : null;
  const styles = lists.styles.filter(
    (s) =>
      !frontend ||
      (s.content as StyleContent).compatible_frontends.includes(frontend),
  );
  const style = prefer(styles, "tailwind", "plain-css");
  const guidance = prefer(lists.guidance, "none", "generic");
  const harness = prefer(lists.harnessPresets, "opencode-native");
  return {
    task: task.pinned_ref,
    variant: "standard",
    stack: stack?.pinned_ref ?? null,
    starter_level: "minimal",
    style: style?.pinned_ref ?? null,
    auth_mode: "contract_bearer",
    guidance: guidance?.pinned_ref ?? null,
    harness_preset: harness?.pinned_ref ?? null,
  };
}

/** The prompt set to preview a template of `mode` with (`app-default` / `slot-single-shot-default`). */
export function defaultPromptSet(
  sets: LibraryItemOut<"prompt_set">[],
  mode: PromptMode = "app",
): LibraryItemOut<"prompt_set"> | undefined {
  const ofMode = sets.filter((s) => s.content.mode === mode);
  return prefer(
    ofMode,
    mode === "app" ? "app-default" : "slot-single-shot-default",
  );
}
