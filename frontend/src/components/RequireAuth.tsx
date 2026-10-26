import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router";

import { useSession } from "../lib/auth";
import { Spinner } from "./ui";

export function RequireAuth({ children }: { children: ReactNode }) {
  const session = useSession();
  const location = useLocation();
  if (session.isPending) return <Spinner label="Checking your session" />;
  if (!session.data?.user) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return children;
}
