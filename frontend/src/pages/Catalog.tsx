import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Search, X } from "lucide-react";
import { type FormEvent, useState } from "react";

import { useToast } from "../lib/toast";
import { Badge, Button, Card, CardHeader, EmptyState, ErrorNote, Input, PageHeader, Select, Spinner } from "../components/ui";
import { cn } from "../lib/cn";
import { api } from "../lib/api";
import { formatMoney } from "../lib/format";
import type { ProductDetail, ProductPage } from "../lib/types";

export function Catalog() {
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  const [selected, setSelected] = useState<string | null>(null);

  const products = useQuery({
    queryKey: ["products", search, category],
    queryFn: () => api<ProductPage>("/api/products", { params: { q: search, category, limit: 200 } }),
    placeholderData: keepPreviousData,
  });

  return (
    <>
      <PageHeader title="Catalog" subtitle="Products, prices, stock, and the names customers use for them." />
      <div className="mb-4 flex flex-wrap gap-3">
        <form
          className="relative w-full sm:w-80"
          onSubmit={(e) => {
            e.preventDefault();
            setSearch(query.trim());
          }}
        >
          <Search className="absolute top-3 left-3 size-4 text-ink-400" aria-hidden />
          <Input
            className="pl-9"
            placeholder="Search name, SKU, brand or alias"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="Search products"
          />
        </form>
        <Select value={category} onChange={(e) => setCategory(e.target.value)} aria-label="Category">
          <option value="">All categories</option>
          {products.data?.categories.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </Select>
      </div>

      <div className={cn("grid gap-6", selected && "lg:grid-cols-[minmax(0,1fr)_24rem]")}>
        <Card>
          {products.isPending ? (
            <Spinner />
          ) : products.error ? (
            <div className="p-5">
              <ErrorNote error={products.error} />
            </div>
          ) : products.data.items.length === 0 ? (
            <EmptyState title="No products match" />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="border-b border-ink-100 text-left text-xs font-medium tracking-wide text-ink-500 uppercase">
                  <tr>
                    <th className="px-5 py-3">Product</th>
                    <th className="px-5 py-3">Category</th>
                    <th className="px-5 py-3 text-right">Price (A)</th>
                    <th className="px-5 py-3 text-right">In stock</th>
                    <th className="px-5 py-3 text-right">Aliases</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-ink-100">
                  {products.data.items.map((p) => (
                    <tr
                      key={p.sku}
                      onClick={() => setSelected(p.sku)}
                      className={cn("cursor-pointer hover:bg-ink-50", selected === p.sku && "bg-brand-50/60")}
                    >
                      <td className="px-5 py-3">
                        <p className="font-medium text-ink-900">{p.name}</p>
                        <p className="text-xs text-ink-400">{p.sku}</p>
                      </td>
                      <td className="px-5 py-3 text-ink-600">{p.category}</td>
                      <td className="px-5 py-3 text-right tabular-nums">
                        {formatMoney(p.prices.A?.[p.default_unit])}
                        <span className="text-xs text-ink-400"> / {p.default_unit}</span>
                      </td>
                      <td className="px-5 py-3 text-right tabular-nums">
                        {p.available === null ? "-" : p.available <= 0 ? <Badge tone="red">Out</Badge> : p.available}
                      </td>
                      <td className="px-5 py-3 text-right text-ink-600">{p.alias_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
        {selected && <ProductPanel sku={selected} onClose={() => setSelected(null)} />}
      </div>
    </>
  );
}

function ProductPanel({ sku, onClose }: { sku: string; onClose: () => void }) {
  const client = useQueryClient();
  const toast = useToast();
  const [alias, setAlias] = useState("");
  const product = useQuery({
    queryKey: ["product", sku],
    queryFn: () => api<ProductDetail>(`/api/products/${sku}`),
  });

  const refresh = (data?: ProductDetail) => {
    if (data) client.setQueryData(["product", sku], data);
    void client.invalidateQueries({ queryKey: ["products"] });
  };
  const add = useMutation({
    mutationFn: (text: string) => api<ProductDetail>(`/api/products/${sku}/aliases`, { method: "POST", body: { text } }),
    onSuccess: (data) => {
      refresh(data);
      setAlias("");
      toast.success("Alias added. The agent will use it within a minute.");
    },
    onError: (e) => toast.error(e.message),
  });
  const remove = useMutation({
    mutationFn: (id: number) => api(`/api/aliases/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["product", sku] });
      refresh();
    },
    onError: (e) => toast.error(e.message),
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    if (alias.trim()) add.mutate(alias.trim());
  }

  return (
    <Card className="h-fit lg:sticky lg:top-8">
      <CardHeader
        title={product.data?.name ?? sku}
        action={
          <button type="button" onClick={onClose} aria-label="Close" className="text-ink-400 hover:text-ink-700">
            <X className="size-4" />
          </button>
        }
      />
      {product.isPending ? (
        <Spinner />
      ) : product.error ? (
        <div className="p-5">
          <ErrorNote error={product.error} />
        </div>
      ) : (
        <div className="space-y-5 p-5 text-sm">
          <table className="w-full">
            <thead className="text-left text-xs text-ink-500">
              <tr>
                <th className="pb-1 font-medium">Unit</th>
                <th className="pb-1 text-right font-medium">Tier A</th>
                <th className="pb-1 text-right font-medium">Tier B</th>
              </tr>
            </thead>
            <tbody>
              {product.data.units.map((u) => (
                <tr key={u.name}>
                  <td className="py-1 text-ink-700">
                    {u.name} {u.base_qty > 1 && <span className="text-xs text-ink-400">({u.base_qty} pcs)</span>}
                  </td>
                  <td className="py-1 text-right tabular-nums">{formatMoney(product.data.prices.A?.[u.name])}</td>
                  <td className="py-1 text-right tabular-nums">{formatMoney(product.data.prices.B?.[u.name])}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div>
            <h3 className="mb-2 text-xs font-medium tracking-wide text-ink-500 uppercase">What customers call it</h3>
            <ul className="flex flex-wrap gap-1.5">
              {product.data.aliases.map((a) => (
                <li key={a.id}>
                  <span className="inline-flex items-center gap-1 rounded-md bg-ink-50 py-0.5 pr-1 pl-2 text-xs text-ink-700 ring-1 ring-ink-200">
                    {a.text}
                    {a.source !== "seed" && <span className="text-[10px] text-brand-700">{a.source}</span>}
                    <button
                      type="button"
                      onClick={() => remove.mutate(a.id)}
                      aria-label={`Remove alias ${a.text}`}
                      className="rounded p-0.5 text-ink-400 hover:bg-ink-200 hover:text-ink-700"
                    >
                      <X className="size-3" />
                    </button>
                  </span>
                </li>
              ))}
            </ul>
            <form onSubmit={submit} className="mt-3 flex gap-2">
              <Input placeholder="e.g. laal dabba chai" value={alias} onChange={(e) => setAlias(e.target.value)} aria-label="New alias" />
              <Button type="submit" variant="secondary" loading={add.isPending}>
                Add
              </Button>
            </form>
          </div>
        </div>
      )}
    </Card>
  );
}
