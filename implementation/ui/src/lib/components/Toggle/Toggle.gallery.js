import Toggle from "./Toggle.svelte";

export default {
  name: "Toggle",
  summary:
    "A binary setting that applies immediately (role=switch, not a checkbox). The label is part of the hit target.",
  component: Toggle,
  axes: ["checked", "disabled", "description"],
  layout: "stack",
  variants: [
    { label: "off", props: { label: "Follow the run log" } },
    { label: "on", props: { label: "Follow the run log", checked: true } },
    {
      label: "with description",
      props: {
        label: "Keep the UI after the run",
        description: "The process stays up while a browser is connected.",
        checked: true,
      },
    },
    {
      label: "disabled",
      props: { label: "Stream intermediate volumes", disabled: true },
    },
  ],
};
