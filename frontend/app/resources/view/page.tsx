"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, FileQuestion, Loader2, Lock } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import ProtectedContent from "@/components/portal/protected-content";
import ShareMenu from "@/components/portal/share-menu";
import { formatSize } from "@/lib/format";
import { useMe } from "@/components/portal/use-me";
import {
  ImageViewer,
  MarkdownViewer,
  PdfCanvasViewer,
  TextViewer,
  VideoViewer,
} from "@/components/portal/viewers";
import {
  contentPdfUrl,
  contentRawUrl,
  getContentMeta,
} from "@/lib/portal-api";

function Spinner() {
  return (
    <div className="grid min-h-[70vh] place-items-center text-muted">
      <Loader2 className="size-6 animate-spin" />
    </div>
  );
}

/** The file id travels as `?fileId=` rather than a path segment: the app is
 *  statically exported, so a dynamic `[fileId]` route has no id to prerender. */
export default function ViewerPage() {
  return (
    <Suspense fallback={<Spinner />}>
      <Viewer />
    </Suspense>
  );
}

function Viewer() {
  const fileId = useSearchParams().get("fileId") ?? "";
  const { data: me, isLoading: meLoading } = useMe();
  const { data: meta, error, isLoading } = useQuery({
    queryKey: ["content-meta", fileId],
    queryFn: () => getContentMeta(fileId),
    enabled: !!me && !!fileId,
    retry: false,
  });

  if (meLoading || (me && !!fileId && isLoading)) return <Spinner />;

  if (!me) {
    return (
      <div className="grid min-h-[70vh] place-items-center">
        <div className="glass max-w-md p-8 text-center">
          <p className="text-sm text-muted">Sign in to view this resource.</p>
          <a
            href="/signin"
            className="btn-primary mt-4 inline-block rounded-xl px-5 py-2.5 text-sm font-semibold text-white"
          >
            Sign in to continue
          </a>
        </div>
      </div>
    );
  }

  if (!fileId) {
    return (
      <div className="grid min-h-[70vh] place-items-center">
        <div className="glass max-w-md p-8 text-center">
          <FileQuestion className="mx-auto mb-3 size-8 text-muted" />
          <h1 className="font-semibold">No file selected</h1>
          <p className="mt-2 text-sm text-muted">
            This link is missing a file reference.
          </p>
          <Link
            href="/resources"
            className="btn-primary mt-5 inline-flex items-center gap-2 rounded-xl px-5 py-2.5 text-sm font-semibold text-white"
          >
            <ArrowLeft className="size-4" /> Back to library
          </Link>
        </div>
      </div>
    );
  }

  if (error) {
    const status = (error as Error & { status?: number }).status;
    return (
      <div className="grid min-h-[70vh] place-items-center">
        <div className="glass max-w-md p-8 text-center">
          <Lock className="mx-auto mb-3 size-8 text-warning" />
          <h1 className="font-semibold">
            {status === 403 ? "No active access" : "Unavailable"}
          </h1>
          <p className="mt-2 text-sm text-muted">
            {status === 403
              ? "You don't have an active grant covering this file. Select it in the library and request access."
              : (error as Error).message}
          </p>
          <Link
            href="/resources"
            className="btn-primary mt-5 inline-flex items-center gap-2 rounded-xl px-5 py-2.5 text-sm font-semibold text-white"
          >
            <ArrowLeft className="size-4" /> Back to library
          </Link>
        </div>
      </div>
    );
  }

  if (!meta) return null;

  const rawUrl = contentRawUrl(fileId, meta.ticket);
  const pdfUrl = contentPdfUrl(fileId, meta.ticket);

  const body = (() => {
    switch (meta.viewer) {
      case "pdf":
        return <PdfCanvasViewer url={rawUrl} email={me.email} />;
      case "office-pdf":
      case "gdoc-pdf":
        return <PdfCanvasViewer url={pdfUrl} email={me.email} />;
      case "md":
        return <MarkdownViewer url={rawUrl} />;
      case "text":
        return <TextViewer url={rawUrl} />;
      case "image":
        return <ImageViewer url={rawUrl} name={meta.name} />;
      case "video":
        return <VideoViewer url={rawUrl} />;
      default:
        return (
          <div className="glass grid place-items-center p-12 text-center">
            <FileQuestion className="mb-3 size-8 text-muted" />
            <p className="text-sm text-muted">
              No in-app viewer for this file type ({meta.mime_type}).
            </p>
          </div>
        );
    }
  })();

  return (
    <div className="mx-auto max-w-5xl px-8 py-8">
      <div className="mb-5 flex items-center gap-3">
        <Link
          href="/resources"
          className="glass grid size-9 shrink-0 place-items-center text-muted transition-colors hover:text-text"
        >
          <ArrowLeft className="size-4" />
        </Link>
        <div className="min-w-0">
          <h1 className="truncate font-semibold">{meta.name}</h1>
          <p className="truncate text-xs text-muted">
            {meta.path}
            {meta.size != null && ` · ${formatSize(meta.size)}`}
          </p>
        </div>
        <ShareMenu
          url={`${typeof window !== "undefined" ? window.location.origin : ""}/resources/view?fileId=${fileId}`}
          title={meta.name}
          className="ml-auto"
        />
      </div>

      <ProtectedContent email={me.email}>{body}</ProtectedContent>
    </div>
  );
}
