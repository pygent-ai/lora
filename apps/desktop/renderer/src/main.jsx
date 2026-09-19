import React from "react";
import { createRoot } from "react-dom/client";

import { ErrorBoundary } from "./app/ErrorBoundary.jsx";
import { waitForDesktopBackend } from "./app/startup.js";

// Leave the HTML startup surface visible while both tasks run in parallel.
Promise.all([import("./app/App.jsx"), import("./app/app.css"), waitForDesktopBackend()])
  .then(([{ App }]) => {
    createRoot(document.getElementById("root")).render(
      <React.StrictMode><ErrorBoundary><App /></ErrorBoundary></React.StrictMode>,
    );
  })
  .catch((error) => {
    const message = document.getElementById("startup-message");
    if (message) message.textContent = `启动失败：${error.message || error}`;
    document.getElementById("startup-progress")?.remove();
    const retry = document.getElementById("startup-retry");
    if (retry) {
      retry.hidden = false;
      retry.addEventListener("click", () => window.location.reload());
    }
  });
