import { Select as SelectPrimitive } from "bits-ui";

import Trigger from "./select-trigger.svelte";
import Content from "./select-content.svelte";
import Item from "./select-item.svelte";
import Group from "./select-group.svelte";
import GroupHeading from "./select-group-heading.svelte";
import Separator from "./select-separator.svelte";
import ScrollUpButton from "./select-scroll-up-button.svelte";
import ScrollDownButton from "./select-scroll-down-button.svelte";
import Simple from "./select-simple.svelte";

export type { SimpleSelectItem } from "./select-simple.svelte";

/** `<Select.Root type="single" bind:value>`; bits-ui root (no markup of its own). */
const Root = SelectPrimitive.Root;

export {
  Root,
  Trigger,
  Content,
  Item,
  Group,
  GroupHeading,
  Separator,
  ScrollUpButton,
  ScrollDownButton,
  Simple,
};
