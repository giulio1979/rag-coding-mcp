import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Client, Provider, cacheExchange, fetchExchange } from "urql";
import { App } from "./App";
import "./styles.css";

const client = new Client({
  url: "/graphql",
  exchanges: [cacheExchange, fetchExchange],
  fetchOptions: () => ({ credentials: "include" }),
});

const root = document.getElementById("root");
if (!root) throw new Error("#root missing");
createRoot(root).render(
  <StrictMode>
    <Provider value={client}>
      <App />
    </Provider>
  </StrictMode>,
);
