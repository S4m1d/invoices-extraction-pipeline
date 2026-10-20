import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Link, Route, Routes } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HomePage } from "./pages/HomePage";
import { RunPage } from "./pages/RunPage";
import { DocumentPage } from "./pages/DocumentPage";
import s from "./app.module.css";

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } });

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <header className={s.header}>
          <Link to="/" className={s.brand}>
            Invoice pipeline tester
          </Link>
        </header>
        <main className={s.main}>
          <Routes>
            <Route path="/" element={<HomePage />} />
            <Route path="/runs/:runId" element={<RunPage />} />
            <Route path="/runs/:runId/documents/:invoiceId" element={<DocumentPage />} />
          </Routes>
        </main>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
