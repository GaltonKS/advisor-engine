import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes, useLocation } from "react-router-dom";
import Layout from "./components/Layout";
import { StepUpProvider } from "./components/StepUp";
import { Loading } from "./components/ui";
import "./index.css";
import "./lib/install"; // listen for the browser's install offer before React mounts
import { trackPageview } from "./lib/analytics";
import { AuthProvider, useAuth } from "./lib/auth";
import ClientWorkspace from "./pages/ClientWorkspace";
import Clients from "./pages/Clients";
import PilotMetrics from "./pages/PilotMetrics";
import Privacy from "./pages/Privacy";
import Security from "./pages/Security";
import SignIn from "./pages/SignIn";

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } });

function App() {
  const { ready, email } = useAuth();
  const { pathname } = useLocation();
  // Public pages report fixed page types; the signed-in app reports its own (Layout).
  const publicPage = pathname === "/privacy" ? "/privacy" : ready && !email ? "/sign-in" : null;
  useEffect(() => { if (publicPage) trackPageview(publicPage); }, [publicPage]);
  // The privacy policy is public: it must be readable before signing up.
  if (pathname === "/privacy") return <Privacy />;
  if (!ready) return <Loading />;
  if (!email) return <SignIn />;
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Clients />} />
        <Route path="clients/:clientId" element={<ClientWorkspace />} />
        <Route path="operator" element={<PilotMetrics />} />
        <Route path="security" element={<Security />} />
      </Route>
    </Routes>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <StepUpProvider>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </StepUpProvider>
      </AuthProvider>
    </QueryClientProvider>
  </StrictMode>,
);
