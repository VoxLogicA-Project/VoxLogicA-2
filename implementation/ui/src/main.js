import { mount } from "svelte";

import "./app.css";
import App from "./App.svelte";
import { connect } from "./lib/connection.js";

connect();

mount(App, { target: document.getElementById("app") });
