import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Navigate, RouterProvider } from "react-router";

import "./index.css";
import { Layout } from "./components/Layout";
import { RequireAuth } from "./components/RequireAuth";
import { ToastProvider } from "./components/toast";
import { ApiError } from "./lib/api";
import { Catalog } from "./pages/Catalog";
import { Customers } from "./pages/Customers";
import { Jobs } from "./pages/Jobs";
import { Login } from "./pages/Login";
import { NotFound } from "./pages/NotFound";
import { OrderReview } from "./pages/OrderReview";
import { Orders } from "./pages/Orders";
import { Simulator } from "./pages/Simulator";

// If a session expires mid-use, send the user back to sign in.
function onApiError(error: unknown) {
  if (error instanceof ApiError && error.status === 401) {
    queryClient.setQueryData(["session"], { user: null });
  }
}

const queryClient = new QueryClient({
  queryCache: new QueryCache({ onError: onApiError }),
  mutationCache: new MutationCache({ onError: onApiError }),
  defaultOptions: {
    queries: {
      retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
      refetchOnWindowFocus: true,
    },
  },
});

const router = createBrowserRouter([
  { path: "/login", element: <Login /> },
  {
    element: (
      <RequireAuth>
        <Layout />
      </RequireAuth>
    ),
    children: [
      { index: true, element: <Navigate to="/orders" replace /> },
      { path: "/orders", element: <Orders /> },
      { path: "/orders/:orderId", element: <OrderReview /> },
      { path: "/catalog", element: <Catalog /> },
      { path: "/customers", element: <Customers /> },
      { path: "/jobs", element: <Jobs /> },
      { path: "/simulator", element: <Simulator /> },
      { path: "*", element: <NotFound /> },
    ],
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <RouterProvider router={router} />
      </ToastProvider>
    </QueryClientProvider>
  </StrictMode>,
);
