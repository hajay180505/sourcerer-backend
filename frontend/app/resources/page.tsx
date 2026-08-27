"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { motion } from "framer-motion";
import {
  Clock,
  FolderLock,
  Loader2,
  Search,
  Send,
  ShoppingCart,
  X,
} from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import FileBrowser, {
  Selection,
  ViewMode,
  ViewSwitcher,
} from "@/components/portal/file-browser";
import { useMe } from "@/components/portal/use-me";
import { isUnlocked } from "@/lib/format";
import {
  CatalogNode,
  createAccessRequest,
  getMyGrants,
  searchCatalog,
} from "@/lib/portal-api";

const DURATION_PRESETS = [7, 30, 90];

function SignInCard() {
  return (
    <div className="grid min-h-[70vh] place-items-center">
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        className="glass max-w-md p-10 text-center"
      >
        <span className="mx-auto mb-4 grid size-14 place-items-center rounded-xl bg-accent-2">
          <FolderLock className="size-7 text-white" />
        </span>
        <h1 className="text-xl font-semibold">The Sourcerer library</h1>
        <p className="mt-2 text-sm leading-relaxed text-muted">
          Browse the academic resource library, request timed access, and read
          everything right here — sign in with your Google account to begin.
        </p>
        <Link
          href="/signin"
          className="btn-primary mt-6 inline-block rounded-md px-6 py-3 text-sm font-semibold text-white"
        >
          Sign in to continue
        </Link>
      </motion.div>
    </div>
  );
}

function RequestModal({
  selection,
  onClose,
}: {
  selection: Selection;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [days, setDays] = useState(30);
  const [message, setMessage] = useState("");
  const nodes = [...selection.selected.values()];

  const mutation = useMutation({
    mutationFn: () =>
      createAccessRequest({
        node_ids: nodes.map((n) => n.id),
        requested_days: days,
        message: message || undefined,
      }),
    onSuccess: () => {
      nodes.forEach((n) => selection.toggle(n));
      queryClient.invalidateQueries({ queryKey: ["my-requests"] });
      queryClient.invalidateQueries({ queryKey: ["me-overview"] });
      onClose();
    },
  });

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4">
      <motion.div
        initial={{ opacity: 0, scale: 0.96 }}
        animate={{ opacity: 1, scale: 1 }}
        className="glass w-full max-w-lg p-6"
      >
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">Request access</h2>
          <button onClick={onClose} className="text-muted hover:text-text">
            <X className="size-5" />
          </button>
        </div>

        <ul className="mt-4 max-h-40 space-y-1 overflow-y-auto text-sm">
          {nodes.map((n) => (
            <li key={n.id} className="truncate text-muted">
              {n.is_folder ? "📁" : "📄"} {n.path}
            </li>
          ))}
        </ul>

        <div className="mt-5">
          <label className="text-xs font-medium uppercase tracking-wide text-muted">
            Access period
          </label>
          <div className="mt-2 flex gap-2">
            {DURATION_PRESETS.map((preset) => (
              <button
                key={preset}
                onClick={() => setDays(preset)}
                className={`rounded-md border px-4 py-2 text-sm transition-colors duration-100 ${
                  days === preset
                    ? "border-accent bg-accent/15 text-text"
                    : "border-border text-muted hover:border-border-strong"
                }`}
              >
                {preset} days
              </button>
            ))}
            <input
              type="number"
              min={1}
              max={365}
              value={days}
              onChange={(event) => setDays(Number(event.target.value))}
              className="w-24 rounded-md border border-border bg-surface-2/80 px-3 py-2 text-sm"
            />
          </div>
        </div>

        <div className="mt-4">
          <label className="text-xs font-medium uppercase tracking-wide text-muted">
            Message to the owner (optional)
          </label>
          <textarea
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            rows={3}
            placeholder="Why do you need these materials?"
            className="mt-2 w-full rounded-md border border-border bg-surface-2/80 px-4 py-3 text-sm placeholder:text-muted/60"
          />
        </div>

        {mutation.isError && (
          <p className="mt-3 text-sm text-danger">
            {(mutation.error as Error).message}
          </p>
        )}

        <button
          onClick={() => mutation.mutate()}
          disabled={mutation.isPending || nodes.length === 0 || days < 1}
          className="btn-primary mt-5 flex w-full items-center justify-center gap-2 rounded-md py-3 text-sm font-semibold text-white"
        >
          {mutation.isPending ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Send className="size-4" />
          )}
          Submit request ({nodes.length} item{nodes.length === 1 ? "" : "s"})
        </button>
      </motion.div>
    </div>
  );
}

export default function LibraryPage() {
  const { data: me, isLoading: meLoading } = useMe();
  const [view, setView] = useState<ViewMode>("grid");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Map<string, CatalogNode>>(new Map());
  const [modalOpen, setModalOpen] = useState(false);

  const selection: Selection = useMemo(
    () => ({
      selected,
      toggle: (node) =>
        setSelected((prev) => {
          const next = new Map(prev);
          if (next.has(node.id)) next.delete(node.id);
          else next.set(node.id, node);
          return next;
        }),
    }),
    [selected]
  );

  const { data: grants } = useQuery({
    queryKey: ["my-grants"],
    queryFn: getMyGrants,
    enabled: !!me,
  });
  const grantedPathIds = useMemo(
    () =>
      (grants?.grants ?? [])
        .map((g) => g.path_ids)
        .filter((p): p is string => !!p),
    [grants]
  );

  const { data: searchData, isFetching: searching } = useQuery({
    queryKey: ["catalog-search", query],
    queryFn: () => searchCatalog(query),
    enabled: !!me && query.trim().length >= 2,
  });

  if (meLoading)
    return (
      <div className="grid min-h-[70vh] place-items-center text-muted">
        <Loader2 className="size-6 animate-spin" />
      </div>
    );
  if (!me) return <SignInCard />;

  return (
    <div className="mx-auto max-w-6xl px-8 py-8">
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.25 }}
        className="flex flex-wrap items-center justify-between gap-4"
      >
        <div>
          <h1 className="text-2xl font-medium tracking-[-0.02em]">Library</h1>
          <p className="mt-1 text-sm text-muted">
            {me.is_admin
              ? "Everything in the archive. Use the visibility toggles to control what users can see and request."
              : "Public materials from the archive. Tick locked items to request timed access — unlocked ones open right here."}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Link
            href="/resources/requests"
            className="glass glass-hover flex items-center gap-2 px-4 py-2 text-sm text-muted hover:text-text"
          >
            <Clock className="size-4" /> My requests
          </Link>
          <ViewSwitcher view={view} onChange={setView} />
        </div>
      </motion.div>

      <div className="relative mt-6">
        <Search className="absolute left-4 top-1/2 size-4 -translate-y-1/2 text-faint" />
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search files and folders…"
          className="w-full rounded-md border border-border bg-surface-2/80 py-2.5 pl-11 pr-4 text-sm placeholder:text-faint focus:border-accent/60 focus:outline-none"
        />
      </div>

      <div className="mt-5">
        {query.trim().length >= 2 ? (
          <div className="glass divide-y divide-border/60">
            {searching ? (
              <div className="p-4 text-sm text-muted">Searching…</div>
            ) : searchData?.results.length ? (
              searchData.results.map((node) => {
                const unlocked =
                  me.is_admin || isUnlocked(node.path_ids, grantedPathIds);
                return (
                  <div
                    key={node.id}
                    className="flex items-center gap-3 px-4 py-2.5 text-sm"
                  >
                    {!unlocked && (
                      <input
                        type="checkbox"
                        checked={selected.has(node.id)}
                        onChange={() => selection.toggle(node)}
                        className="size-3.5 accent-accent"
                      />
                    )}
                    {!node.is_folder && unlocked ? (
                      <Link
                        href={`/resources/view?fileId=${node.id}`}
                        className="truncate hover:text-accent hover:underline"
                      >
                        {node.path}
                      </Link>
                    ) : (
                      <span className="truncate text-muted">{node.path}</span>
                    )}
                  </div>
                );
              })
            ) : (
              <div className="p-4 text-sm text-muted">No matches.</div>
            )}
          </div>
        ) : (
          <FileBrowser
            rootLabel="Library"
            selection={selection}
            grantedPathIds={grantedPathIds}
            isAdmin={me.is_admin}
            view={view}
          />
        )}
      </div>

      {selected.size > 0 && (
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          animate={{ opacity: 1, y: 0 }}
          className="fixed bottom-6 left-1/2 z-40 ml-30 -translate-x-1/2"
        >
          <button
            onClick={() => setModalOpen(true)}
            className="btn-primary flex items-center gap-2.5 rounded-xl px-6 py-3.5 text-sm font-semibold text-white"
          >
            <ShoppingCart className="size-4.5" />
            Request access to {selected.size} item{selected.size === 1 ? "" : "s"}
          </button>
        </motion.div>
      )}

      {modalOpen && (
        <RequestModal selection={selection} onClose={() => setModalOpen(false)} />
      )}
    </div>
  );
}
