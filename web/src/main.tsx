import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { initTokenFromLocation } from "./api";
import { App } from "./App";
import "./styles/index.css";

initTokenFromLocation();

const container = document.getElementById("root");
if (!container) {
  throw new Error("#root not found");
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
