import { mount } from "svelte";
import App from "./App.svelte";
import "./app.css";

const target = document.getElementById("app");
if (!(target instanceof HTMLElement)) {
  throw new Error("Missing #app root element.");
}

mount(App, { target });
