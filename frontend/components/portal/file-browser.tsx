"use client";

/** Google-Drive-style browser: breadcrumbs + descend-into-folders, with
 * grid / list / graph views and labeled Folders / Files sections. Shared by
 * the Library (full index, with request-access selection) and Accessible
 * (granted content only) pages. The current folder is mirrored into
 * ?folder=<id> so folder links are shareable. */

import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  ChevronRight,
  CircleDashed,
  FileText,
  Film,
  Folder,
  Globe,
  Image as ImageIcon,
  LayoutGrid,
  List,
  Loader2,
  Lock,
  LockOpen,
  Network,
  Presentation,
  Table2,
  Waypoints,
} from "lucide-react";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import ResourceGraph from "@/components/portal/resource-graph";
import ShareMenu from "@/components/portal/share-menu";
import { formatSize, isUnlocked } from "@/lib/format";
import {
  CatalogNode,
  MyGrant,
  adminSetVisibility,
  getChildren,
  getMyGrants,
} from "@/lib/portal-api";

export type ViewMode = "grid" | "list" | "graph";

export interface Selection {
  selected: Map<string, CatalogNode>;
  toggle: (node: CatalogNode) => void;
}

interface Crumb {
  id: string;
  name: string;
}

export function fileIcon(name: string, mime: string) {
  const ext = name.includes(".") ? name.split(".").pop()!.toLowerCase() : "";
  if (mime.startsWith("image/")) return ImageIcon;
  if (mime.startsWith("video/")) return Film;
  if (mime.includes("presentation") || ext === "ppt" || ext === "pptx")
    return Presentation;
  if (mime.includes("spreadsheet") || ext === "csv" || ext === "xlsx")
    return Table2;
  return FileText;
}

function shareUrl(node: { id: string; is_folder: boolean }): string {
  const origin = typeof window !== "undefined" ? window.location.origin : "";
  return node.is_folder
    ? `${origin}/resources?folder=${node.id}`
    : `${origin}/resources/view/${node.id}`;
}

export function ViewSwitcher({
  view,
  onChange,
}: {
  view: ViewMode;
  onChange: (view: ViewMode) => void;
}) {
  const options = [
    ["grid", LayoutGrid, "Grid"],
    ["list", List, "List"],
    ["graph", Waypoints, "Graph"],
  ] as const;
  return (
    <div className="glass flex p-1">
      {options.map(([key, Icon, label]) => (
        <button
          key={key}
          onClick={() => onChange(key)}
          title={label}
          className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm transition-colors duration-100 ${
            view === key
              ? "bg-white/[0.07] text-text"
              : "text-muted hover:text-text"
          }`}
        >
          <Icon className="size-4" />
          <span className="hidden sm:inline">{label}</span>
        </button>
      ))}
    </div>
  );
}

function SectionLabel({ label, count }: { label: string; count: number }) {
  return (
    <h3 className="mb-2.5 flex items-baseline gap-2 text-xs font-semibold uppercase tracking-[0.12em] text-muted">
      {label}
      <span className="font-normal normal-case tracking-normal text-faint">
        {count}
      </span>
    </h3>
  );
}

function LockBadge({ unlocked }: { unlocked: boolean }) {
  return unlocked ? (
    <LockOpen className="size-3.5 shrink-0 text-success" />
  ) : (
    <Lock className="size-3.5 shrink-0 text-faint" />
  );
}

/** Admin tri-state visibility control: inherit → public → private → inherit.
 * Shows the explicit setting (solid) or the inherited effective state
 * (dashed), and public/total descendant counts for folders. */
function VisibilityToggle({ node }: { node: CatalogNode }) {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: (visibility: "public" | "private" | null) =>
      adminSetVisibility(node.id, visibility),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["catalog-children"] }),
  });
  const explicit = node.visibility ?? null;
  const next =
    explicit === null ? "public" : explicit === "public" ? "private" : null;
  const effectivePublic = node.effective_public === true;
  const Icon =
    explicit === "public" ? Globe : explicit === "private" ? Lock : CircleDashed;
  const label =
    explicit ??
    (effectivePublic ? "inherits · public" : "inherits · private");
  return (
    <button
      onClick={(event) => {
        event.stopPropagation();
        mutation.mutate(next);
      }}
      disabled={mutation.isPending}
      title={`Visibility: ${label} — click to make ${next ?? "inherited"}`}
      className={`flex shrink-0 items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] font-medium transition-colors duration-100 ${
        explicit === "public"
          ? "bg-success/15 text-success"
          : explicit === "private"
            ? "bg-danger/15 text-danger"
            : effectivePublic
              ? "text-success/70 hover:bg-white/[0.06]"
              : "text-faint hover:bg-white/[0.06]"
      }`}
    >
      {mutation.isPending ? (
        <Loader2 className="size-3 animate-spin" />
      ) : (
        <Icon className="size-3" />
      )}
      {explicit ? explicit : effectivePublic ? "public" : "private"}
    </button>
  );
}

function SelectBox({
  node,
  selection,
  unlocked,
}: {
  node: CatalogNode;
  selection?: Selection;
  unlocked: boolean;
}) {
  // Structural containers (visible only so their public contents stay
  // reachable) are not requestable.
  if (!selection || unlocked || node.effective_public === false) return null;
  return (
    <input
      type="checkbox"
      checked={selection.selected.has(node.id)}
      onChange={() => selection.toggle(node)}
      onClick={(event) => event.stopPropagation()}
      title="Add to access request"
      className="size-3.5 shrink-0 accent-accent"
    />
  );
}

export default function FileBrowser({
  rootLabel,
  accessibleOnly = false,
  selection,
  grantedPathIds,
  isAdmin,
  view,
}: {
  rootLabel: string;
  accessibleOnly?: boolean;
  selection?: Selection;
  grantedPathIds: string[];
  isAdmin: boolean;
  view: ViewMode;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const [crumbs, setCrumbs] = useState<Crumb[]>([]); // empty = at the root
  // A ?folder=<id> deep link: browse it immediately, rebuild crumbs from the
  // folder's materialized path once its listing arrives.
  const [pendingLink, setPendingLink] = useState<string | null>(() =>
    typeof window !== "undefined"
      ? new URLSearchParams(window.location.search).get("folder")
      : null
  );
  const current = crumbs[crumbs.length - 1];
  const folderId = pendingLink ?? current?.id ?? "root";

  const virtualRoot =
    accessibleOnly && !isAdmin && !current && !pendingLink;

  const { data: grantsData, isLoading: grantsLoading } = useQuery({
    queryKey: ["my-grants"],
    queryFn: getMyGrants,
    enabled: virtualRoot,
  });

  const {
    data: childrenData,
    isLoading: childrenLoading,
    isError: childrenError,
    isPlaceholderData,
  } = useQuery({
    queryKey: ["catalog-children", folderId],
    queryFn: () => getChildren(folderId),
    staleTime: 5 * 60_000,
    // Entering a new folder keeps the previous listing on screen (dimmed)
    // instead of collapsing to a spinner; visited folders render instantly.
    placeholderData: keepPreviousData,
    enabled: !virtualRoot,
    retry: 1,
  });

  // Warm the cache for a subfolder the moment the cursor reaches its card, so
  // the click usually lands on already-loaded data.
  const queryClient = useQueryClient();
  const prefetchFolder = (id: string) =>
    queryClient.prefetchQuery({
      queryKey: ["catalog-children", id],
      queryFn: () => getChildren(id),
      staleTime: 5 * 60_000,
    });

  // Resolve a deep link into breadcrumbs (parent carries the full path). If the
  // linked folder can't be loaded (swept from the catalog, network error), fall
  // back to the root rather than leaving pendingLink stuck true forever.
  useEffect(() => {
    if (!pendingLink) return;
    // Placeholder data belongs to the previously viewed folder — deciding on
    // it would clear the deep link before its own listing ever arrives.
    if (childrenData && !isPlaceholderData) {
      const parent = childrenData.parent;
      if (parent.id === pendingLink) {
        const ids = parent.path_ids.split("/").filter(Boolean);
        const names = parent.path.split("/");
        setCrumbs(
          ids.slice(1).map((id, i) => ({ id, name: names[i + 1] ?? "…" }))
        );
      }
      setPendingLink(null);
    } else if (childrenError) {
      setPendingLink(null);
      setCrumbs([]);
    }
  }, [pendingLink, childrenData, childrenError, isPlaceholderData]);

  // Keep the URL shareable: mirror the current folder into ?folder=.
  useEffect(() => {
    if (pendingLink) return;
    const url = current ? `${pathname}?folder=${current.id}` : pathname;
    window.history.replaceState(null, "", url);
  }, [current, pathname, pendingLink]);

  const unlockedFor = (pathIds: string) =>
    isAdmin || isUnlocked(pathIds, grantedPathIds);

  const openFolder = (id: string, name: string) =>
    setCrumbs((prev) => [...prev, { id, name }]);

  const openItem = (node: CatalogNode, unlocked: boolean) => {
    if (node.is_folder) openFolder(node.id, node.name);
    else if (unlocked) router.push(`/resources/view/${node.id}`);
    else selection?.toggle(node);
  };

  const grantNodes: CatalogNode[] = (grantsData?.grants ?? [])
    .filter((grant): grant is MyGrant & { path_ids: string } => !!grant.path_ids)
    .map((grant) => ({
      id: grant.node_id,
      parent_id: null,
      name: grant.name,
      is_folder: grant.is_folder,
      mime_type: grant.is_folder ? "application/vnd.google-apps.folder" : "",
      size: null,
      modified_time: null,
      path: grant.path ?? grant.name,
      path_ids: grant.path_ids,
    }));

  const items = virtualRoot ? grantNodes : (childrenData?.children ?? []);
  const loading = virtualRoot ? grantsLoading : childrenLoading || !!pendingLink;
  const folders = items.filter((item) => item.is_folder);
  const files = items.filter((item) => !item.is_folder);

  return (
    <div>
      {/* Breadcrumbs + current-folder share */}
      <div className="mb-4 flex items-center justify-between gap-3">
        <nav className="flex min-w-0 flex-wrap items-center gap-1 text-sm">
          <button
            onClick={() => setCrumbs([])}
            className={`rounded-md px-2 py-1 transition-colors duration-100 ${
              crumbs.length
                ? "text-muted hover:bg-white/[0.05] hover:text-text"
                : "font-medium text-text"
            }`}
          >
            {rootLabel}
          </button>
          {crumbs.map((crumb, i) => (
            <span key={crumb.id} className="flex items-center gap-1">
              <ChevronRight className="size-3.5 shrink-0 text-faint" />
              <button
                onClick={() => setCrumbs(crumbs.slice(0, i + 1))}
                className={`max-w-48 truncate rounded-md px-2 py-1 transition-colors duration-100 ${
                  i === crumbs.length - 1
                    ? "font-medium text-text"
                    : "text-muted hover:bg-white/[0.05] hover:text-text"
                }`}
              >
                {crumb.name}
              </button>
            </span>
          ))}
        </nav>
        {current && (
          <ShareMenu
            url={shareUrl({ id: current.id, is_folder: true })}
            title={current.name}
            className="shrink-0"
          />
        )}
      </div>

      {/* Content */}
      {view === "graph" ? (
        virtualRoot ? (
          <div className="glass grid min-h-72 place-items-center px-6 py-12 text-center">
            <div>
              <Network className="mx-auto mb-3 size-7 text-faint" />
              <p className="text-sm text-muted">
                Open one of your folders to see its graph.
              </p>
            </div>
          </div>
        ) : (
          <ResourceGraph rootId={folderId} onOpenFolder={openFolder} />
        )
      ) : loading ? (
        <div className="glass grid min-h-72 place-items-center text-muted">
          <Loader2 className="size-6 animate-spin" />
        </div>
      ) : items.length === 0 ? (
        <div className="glass px-6 py-14 text-center text-sm text-muted">
          {virtualRoot
            ? "Nothing unlocked yet — browse the Library and request access."
            : "This folder is empty."}
        </div>
      ) : view === "grid" ? (
        <div
          className={`space-y-7 transition-opacity duration-150 ${
            isPlaceholderData ? "opacity-50" : ""
          }`}
        >
          {folders.length > 0 && (
            <section>
              <SectionLabel label="Folders" count={folders.length} />
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
                {folders.map((node) => {
                  const unlocked = unlockedFor(node.path_ids);
                  // Private container shown only so its public contents stay
                  // reachable — render it bare (dimmed, no request checkbox).
                  const structural =
                    !isAdmin && !unlocked && node.effective_public === false;
                  return (
                    <div
                      key={node.id}
                      role="button"
                      tabIndex={0}
                      onClick={() => openItem(node, unlocked)}
                      onKeyDown={(e) => e.key === "Enter" && openItem(node, unlocked)}
                      onMouseEnter={() => prefetchFolder(node.id)}
                      className="glass glass-hover group flex cursor-pointer items-center gap-3 px-4 py-3 text-left"
                    >
                      <Folder
                        className={`size-5 shrink-0 ${structural ? "text-faint" : "text-accent"}`}
                      />
                      <span className="min-w-0 flex-1">
                        <span
                          className={`block truncate text-sm ${structural ? "text-muted" : ""}`}
                        >
                          {node.name}
                        </span>
                        <span className="block text-[11px] text-faint">
                          {node.child_count ?? 0} items
                          {isAdmin && node.desc_count != null
                            ? ` · ${node.public_count ?? 0}/${node.desc_count} public`
                            : ""}
                        </span>
                      </span>
                      {isAdmin ? (
                        <VisibilityToggle node={node} />
                      ) : (
                        <LockBadge unlocked={unlocked} />
                      )}
                      <SelectBox
                        node={node}
                        selection={selection}
                        unlocked={unlocked}
                      />
                      <ShareMenu url={shareUrl(node)} title={node.name} />
                    </div>
                  );
                })}
              </div>
            </section>
          )}
          {files.length > 0 && (
            <section>
              <SectionLabel label="Files" count={files.length} />
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
                {files.map((node) => {
                  const unlocked = unlockedFor(node.path_ids);
                  const Icon = fileIcon(node.name, node.mime_type);
                  return (
                    <div
                      key={node.id}
                      role="button"
                      tabIndex={0}
                      onClick={() => openItem(node, unlocked)}
                      onKeyDown={(e) => e.key === "Enter" && openItem(node, unlocked)}
                      className="glass glass-hover group flex cursor-pointer flex-col gap-3 p-4 text-left"
                    >
                      <div className="flex w-full items-center justify-between">
                        <Icon
                          className={`size-5 ${unlocked ? "text-accent" : "text-faint"}`}
                        />
                        <span className="flex items-center gap-2">
                          {isAdmin ? (
                            <VisibilityToggle node={node} />
                          ) : (
                            <LockBadge unlocked={unlocked} />
                          )}
                          <SelectBox
                            node={node}
                            selection={selection}
                            unlocked={unlocked}
                          />
                          <ShareMenu url={shareUrl(node)} title={node.name} />
                        </span>
                      </div>
                      <span className="min-w-0">
                        <span
                          className={`block truncate text-sm ${unlocked ? "" : "text-muted"}`}
                        >
                          {node.name}
                        </span>
                        <span className="mt-0.5 block text-[11px] text-faint">
                          {formatSize(node.size) || "—"}
                        </span>
                      </span>
                    </div>
                  );
                })}
              </div>
            </section>
          )}
        </div>
      ) : (
        <div
          className={`space-y-7 transition-opacity duration-150 ${
            isPlaceholderData ? "opacity-50" : ""
          }`}
        >
          {(
            [
              ["Folders", folders],
              ["Files", files],
            ] as const
          ).map(
            ([label, group]) =>
              group.length > 0 && (
                <section key={label}>
                  <SectionLabel label={label} count={group.length} />
                  <div className="glass divide-y divide-border/60">
                    {group.map((node) => {
                      const unlocked = unlockedFor(node.path_ids);
                      const structural =
                        !isAdmin && !unlocked && node.effective_public === false;
                      const Icon = node.is_folder
                        ? Folder
                        : fileIcon(node.name, node.mime_type);
                      return (
                        <div
                          key={node.id}
                          role="button"
                          tabIndex={0}
                          onClick={() => openItem(node, unlocked)}
                          onKeyDown={(e) =>
                            e.key === "Enter" && openItem(node, unlocked)
                          }
                          onMouseEnter={() =>
                            node.is_folder && prefetchFolder(node.id)
                          }
                          className="flex w-full cursor-pointer items-center gap-3 px-4 py-2.5 text-left text-sm transition-colors duration-100 hover:bg-white/[0.04]"
                        >
                          <Icon
                            className={`size-4.5 shrink-0 ${
                              (node.is_folder && !structural) || unlocked
                                ? "text-accent"
                                : "text-faint"
                            }`}
                          />
                          <span
                            className={`min-w-0 flex-1 truncate ${
                              (unlocked || node.is_folder) && !structural
                                ? ""
                                : "text-muted"
                            }`}
                          >
                            {node.name}
                          </span>
                          <span className="w-20 shrink-0 text-right text-xs text-faint">
                            {node.is_folder
                              ? `${node.child_count ?? 0} items`
                              : formatSize(node.size) || "—"}
                          </span>
                          {isAdmin && node.is_folder && node.desc_count != null && (
                            <span className="shrink-0 text-[11px] text-faint">
                              {node.public_count ?? 0}/{node.desc_count} public
                            </span>
                          )}
                          <span
                            className={`flex shrink-0 items-center justify-end gap-2.5 ${
                              isAdmin ? "w-32" : "w-24"
                            }`}
                          >
                            {isAdmin ? (
                              <VisibilityToggle node={node} />
                            ) : (
                              <LockBadge unlocked={unlocked} />
                            )}
                            <SelectBox
                              node={node}
                              selection={selection}
                              unlocked={unlocked}
                            />
                            <ShareMenu url={shareUrl(node)} title={node.name} />
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </section>
              )
          )}
        </div>
      )}
    </div>
  );
}
