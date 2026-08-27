"use client";

import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import {
  ArrowRight,
  Bell,
  CheckCircle2,
  Clock,
  FileText,
  Folder,
  FolderLock,
  History,
  Loader2,
  LockOpen,
  XCircle,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { useMe } from "@/components/portal/use-me";
import { getMyOverview } from "@/lib/portal-api";

function daysLeft(expiresAt: string): string {
  const ms = new Date(expiresAt).getTime() - Date.now();
  if (ms <= 0) return "expired";
  const days = Math.floor(ms / 86_400_000);
  return days > 0 ? `${days}d left` : `${Math.floor(ms / 3_600_000)}h left`;
}

function timeAgo(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const minutes = Math.floor(ms / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

const REQUEST_NOTICE: Record<
  string,
  { icon: typeof Bell; tone: string; text: (items: number) => string }
> = {
  pending: {
    icon: Clock,
    tone: "border-warning/30 bg-warning/10 text-warning",
    text: (items) =>
      `Your request for ${items} item${items === 1 ? "" : "s"} is waiting for the owner's review.`,
  },
  approved: {
    icon: CheckCircle2,
    tone: "border-success/30 bg-success/10 text-success",
    text: (items) =>
      `Your latest request was approved — ${items} item${items === 1 ? "" : "s"} unlocked below.`,
  },
  denied: {
    icon: XCircle,
    tone: "border-danger/30 bg-danger/10 text-danger",
    text: () =>
      "Your latest request was declined. You can ask again with a note for the owner.",
  },
  cancelled: {
    icon: XCircle,
    tone: "border-border bg-white/[0.04] text-muted",
    text: () => "Your latest request was cancelled.",
  },
};

export default function HomePage() {
  const router = useRouter();
  const { data: me, isLoading: meLoading } = useMe();
  const { data: overview, isLoading } = useQuery({
    queryKey: ["me-overview"],
    queryFn: getMyOverview,
    enabled: !!me,
  });

  useEffect(() => {
    if (!meLoading && !me) router.replace("/signin");
  }, [me, meLoading, router]);

  if (meLoading || !me || isLoading)
    return (
      <div className="grid min-h-[70vh] place-items-center text-muted">
        <Loader2 className="size-6 animate-spin" />
      </div>
    );

  const firstName = (me.name ?? me.email).split(" ")[0];
  const notice = overview?.latest_request
    ? REQUEST_NOTICE[overview.latest_request.status]
    : null;

  return (
    <div className="mx-auto max-w-5xl px-8 py-10">
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.25 }}
      >
        <h1 className="text-[28px] font-medium tracking-[-0.02em]">
          Hey {firstName} 👋
        </h1>
        <p className="mt-1 text-sm text-muted">
          Your library at a glance — what you can read, and where you left off.
        </p>
      </motion.div>

      {/* Latest request status */}
      {notice && overview?.latest_request && (
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.05, duration: 0.25 }}
          className={`mt-6 flex items-center gap-3 rounded-xl border px-4.5 py-3.5 text-sm ${notice.tone}`}
        >
          <notice.icon className="size-4.5 shrink-0" />
          <span className="flex-1">
            {notice.text(overview.latest_request.items)}
          </span>
          <Link
            href="/resources/requests"
            className="shrink-0 text-xs font-medium underline-offset-2 hover:underline"
          >
            Details
          </Link>
        </motion.div>
      )}

      <div className="mt-8 grid gap-6 lg:grid-cols-5">
        {/* Your access */}
        <motion.section
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.1, duration: 0.25 }}
          className="lg:col-span-3"
        >
          <div className="mb-3 flex items-center justify-between">
            <h2 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-muted">
              <LockOpen className="size-4 text-success" /> Your access
            </h2>
            <Link
              href="/resources"
              className="flex items-center gap-1 text-xs font-medium text-accent hover:underline"
            >
              Browse library <ArrowRight className="size-3.5" />
            </Link>
          </div>

          {overview?.grants.length ? (
            <div className="glass divide-y divide-border/60">
              {overview.grants.map((grant) => (
                <div
                  key={grant.id}
                  className="flex items-center gap-3 px-5 py-3 text-sm"
                >
                  {grant.is_folder ? (
                    <Folder className="size-4 shrink-0 text-accent" />
                  ) : (
                    <FileText className="size-4 shrink-0 text-faint" />
                  )}
                  {grant.is_folder ? (
                    <span className="truncate">{grant.path ?? grant.name}</span>
                  ) : (
                    <Link
                      href={`/resources/view?fileId=${grant.node_id}`}
                      className="truncate hover:text-accent hover:underline"
                    >
                      {grant.path ?? grant.name}
                    </Link>
                  )}
                  <span className="ml-auto shrink-0 rounded-full bg-success/10 px-2.5 py-0.5 text-[11px] text-success">
                    {daysLeft(grant.expires_at)}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <div className="glass px-6 py-10 text-center">
              <FolderLock className="mx-auto mb-3 size-7 text-faint" />
              <p className="text-sm text-muted">
                {me.is_admin
                  ? "You're the library owner — everything is already open to you."
                  : "Nothing unlocked yet. Browse the index and request the folders you need."}
              </p>
              <Link
                href="/resources"
                className="btn-primary mt-5 inline-flex items-center gap-2 rounded-md px-5 py-2.5 text-sm font-semibold text-white"
              >
                Explore the library <ArrowRight className="size-4" />
              </Link>
            </div>
          )}
        </motion.section>

        {/* Recently read */}
        <motion.section
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.15, duration: 0.25 }}
          className="lg:col-span-2"
        >
          <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-muted">
            <History className="size-4" /> Recently read
          </h2>
          {overview?.recent_views.length ? (
            <div className="glass divide-y divide-border/60">
              {overview.recent_views.map((view) => (
                <Link
                  key={view.node_id}
                  href={`/resources/view?fileId=${view.node_id}`}
                  className="group block px-5 py-3"
                >
                  <div className="truncate text-sm group-hover:text-accent">
                    {view.name}
                  </div>
                  <div className="mt-0.5 flex items-center justify-between gap-2 text-[11px] text-faint">
                    <span className="truncate">{view.path}</span>
                    <span className="shrink-0">{timeAgo(view.viewed_at)}</span>
                  </div>
                </Link>
              ))}
            </div>
          ) : (
            <div className="glass px-5 py-8 text-center text-sm text-muted">
              Materials you open will show up here so you can jump back in.
            </div>
          )}
        </motion.section>
      </div>
    </div>
  );
}
