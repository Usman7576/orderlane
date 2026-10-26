import { type FormEvent, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router";

import { Button, ErrorNote, Input } from "../components/ui";
import { useLogin, useSession } from "../lib/auth";

export function Login() {
  const session = useSession();
  const login = useLogin();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const from = (location.state as { from?: string } | null)?.from ?? "/orders";

  if (session.data?.user) return <Navigate to={from} replace />;

  function submit(event: FormEvent) {
    event.preventDefault();
    login.mutate({ email, password }, { onSuccess: () => navigate(from, { replace: true }) });
  }

  return (
    <div className="grid min-h-screen place-items-center bg-ink-50 px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <div className="mx-auto mb-4 grid size-11 place-items-center rounded-xl bg-brand-600 font-bold text-white">
            OL
          </div>
          <h1 className="text-xl font-semibold text-ink-900">Sign in to OrderLane</h1>
          <p className="mt-1 text-sm text-ink-500">Review WhatsApp orders before they reach customers.</p>
        </div>
        <form onSubmit={submit} className="space-y-4 rounded-xl border border-ink-200 bg-white p-6 shadow-xs">
          {login.error && <ErrorNote error={login.error} />}
          <label className="block text-sm font-medium text-ink-800">
            Email
            <Input
              className="mt-1.5"
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </label>
          <label className="block text-sm font-medium text-ink-800">
            Password
            <Input
              className="mt-1.5"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          <Button type="submit" className="w-full" loading={login.isPending}>
            Sign in
          </Button>
        </form>
        <p className="mt-6 text-center text-xs text-ink-400">Staff accounts are created by the business owner.</p>
      </div>
    </div>
  );
}
