import React from "react";
import { createRoot } from "react-dom/client";
import { Customer } from "../../shared/App";
import "@fontsource-variable/inter";
import "@fontsource-variable/plus-jakarta-sans";
import "../../shared/style.css";
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Customer />
  </React.StrictMode>,
);
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js");
