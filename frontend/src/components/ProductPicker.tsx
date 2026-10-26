import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useEffect, useState } from "react";

import { api } from "../lib/api";
import { formatMoney } from "../lib/format";
import type { Product, ProductPage } from "../lib/types";
import { Input } from "./ui";

/** Search the catalog and pick a product. Searches names, SKUs, brands and aliases. */
export function ProductPicker({
  initialQuery = "",
  onPick,
  onCancel,
}: {
  initialQuery?: string;
  onPick: (product: Product) => void;
  onCancel: () => void;
}) {
  const [query, setQuery] = useState(initialQuery);
  const [debounced, setDebounced] = useState(initialQuery);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(query.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [query]);

  const results = useQuery({
    queryKey: ["product-search", debounced],
    queryFn: () => api<ProductPage>("/api/products", { params: { q: debounced, limit: 8 } }),
    enabled: debounced.length >= 2,
  });

  return (
    <div className="rounded-lg border border-brand-200 bg-brand-50/40 p-3">
      <div className="relative">
        <Search className="absolute top-3 left-3 size-4 text-ink-400" aria-hidden />
        <Input
          autoFocus
          className="pl-9"
          placeholder="Search products, brands or what customers call them"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Escape" && onCancel()}
          aria-label="Search products"
        />
      </div>
      <ul className="mt-2 max-h-64 overflow-y-auto">
        {results.data?.items.map((p) => (
          <li key={p.sku}>
            <button
              type="button"
              onClick={() => onPick(p)}
              className="flex w-full items-center justify-between gap-3 rounded-md px-3 py-2 text-left text-sm hover:bg-white"
            >
              <span>
                <span className="font-medium text-ink-900">{p.name}</span>
                <span className="ml-2 text-xs text-ink-400">{p.sku}</span>
              </span>
              <span className="text-xs text-ink-500">
                {formatMoney(p.prices.A?.[p.default_unit])} / {p.default_unit}
              </span>
            </button>
          </li>
        ))}
        {debounced.length >= 2 && results.data?.items.length === 0 && (
          <li className="px-3 py-2 text-sm text-ink-500">No products match.</li>
        )}
      </ul>
      <div className="mt-2 text-right">
        <button type="button" onClick={onCancel} className="text-xs text-ink-500 hover:text-ink-800">
          Cancel
        </button>
      </div>
    </div>
  );
}
