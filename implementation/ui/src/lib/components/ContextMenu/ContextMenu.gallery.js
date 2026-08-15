import ContextMenu from "./ContextMenu.svelte";

export default {
  name: "ContextMenu",
  summary:
    "Actions for the region you pointed at. Right-click inside the target, or Shift+F10 from the keyboard; arrows walk it, Escape closes it.",
  component: ContextMenu,
  axes: ["items", "label"],
  layout: "stack",
  variants: [
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
