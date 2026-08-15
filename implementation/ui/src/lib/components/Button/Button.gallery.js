import Button from "./Button.svelte";

/**
 * The gallery entry is not documentation *about* Button -- it is the list of
 * states Button supports, and the gallery renders the real component from it.
 * Adding a tone without adding it here means the gallery is wrong, which is why
 * a test asserts every component has one of these.
 */
export default {
  name: "Button",
  summary:
    "The only clickable affordance. Tone says how loud the action is, not what it does; at most one accent button per view.",
  component: Button,
  axes: ["tone", "size", "disabled"],
  variants: [
    { label: "accent", props: { tone: "accent" }, text: "Run" },
    { label: "neutral", props: {}, text: "Cancel" },
    { label: "quiet", props: { tone: "quiet" }, text: "Details" },
    { label: "danger", props: { tone: "danger" }, text: "Delete cache" },
    { label: "small", props: { size: "sm" }, text: "Copy" },
    { label: "small accent", props: { tone: "accent", size: "sm" }, text: "Open" },
    { label: "disabled", props: { disabled: true }, text: "Run" },
    {
      label: "disabled accent",
      props: { tone: "accent", disabled: true },
      text: "Run",
    },
  ],
};
