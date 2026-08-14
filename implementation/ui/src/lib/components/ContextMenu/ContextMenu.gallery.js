import ContextMenu from "./ContextMenu.svelte";

export default {
  name: "ContextMenu",
  summary:
    "Actions for the region you pointed at, or for a menu button. Right-click inside the target, click the trigger, or Shift+F10 from the keyboard; arrows walk it, Escape closes it.",
  component: ContextMenu,
  axes: ["items", "label", "trigger"],
  layout: "stack",
  variants: [
    {
      // The same component with a trigger instead of a region: one keyboard
      // model, two ways in. The dev design panel is opened by exactly this.
      label: "as a menu button",
      triggerLabel: "Menu ▾",
      props: {
        label: "Example menu",
        items: [
          { label: "Moodboard", hint: "#design/moodboard" },
          { label: "Palette", hint: "#design/palette" },
          { separator: true },
          { label: "Reset layout", danger: true },
        ],
      },
    },
    {
      label: "plain",
      text: "Right-click inside this area",
      stage: "region",
      props: {
        items: [
          { label: "Copy node id", hint: "⌘C" },
          { label: "Reveal in graph" },
        ],
      },
    },
    {
      label: "separators, hints, disabled, danger",
      text: "Right-click inside this area",
      stage: "region",
      props: {
        items: [
          { label: "Open", hint: "⏎" },
          { label: "Copy value", hint: "⌘C" },
          { separator: true },
          { label: "Recompute", disabled: true },
          { separator: true },
          { label: "Evict from cache", danger: true },
        ],
      },
    },
  ],
};
