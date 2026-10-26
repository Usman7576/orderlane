import { Link } from "react-router";

export function NotFound() {
  return (
    <div className="py-24 text-center">
      <p className="text-sm font-medium text-brand-700">404</p>
      <h1 className="mt-2 text-xl font-semibold text-ink-900">Page not found</h1>
      <Link to="/orders" className="mt-4 inline-block text-sm text-brand-700 hover:underline">
        Back to orders
      </Link>
    </div>
  );
}
