import Card from "./Card.svelte";

export default {
  name: "Card",
  summary:
    "The only container. Elevation comes from a border and a surface change, not a shadow, so a dense screen stays readable.",
  component: Card,
  axes: ["title", "subtitle", "actions", "flush"],
  layout: "stack",
  variants: [
    { label: "titled", props: { title: "Instance" }, text: "Body content." },
    {
      label: "with subtitle",
      props: { title: "Run", subtitle: "brats021.imgql — 369 cases" },
      text: "Body content.",
    },
    { label: "bare", props: {}, text: "No header at all." },
  ],
};
