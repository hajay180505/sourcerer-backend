"use client";

/** Force graph of a folder subtree. Controlled by the surrounding browser:
 * the parent owns the current root (breadcrumbs) and is notified when the
 * user drills into a folder node. Files open in the viewer. */

import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { getGraph } from "@/lib/portal-api";

const ForceGraph2D = dynamic(() => import("react-force-graph-2d"), {
  ssr: false,
});

// File-type hues — data-viz color carries meaning here, so the map stays
// multi-hue by design; UI chrome around it remains purple/neutral.
const EXT_COLORS: Record<string, string> = {
  pdf: "#f87171",
  md: "#a78bfa",
  txt: "#9e9e9e",
  sql: "#fbbf24",
  ppt: "#fb923c",
  pptx: "#fb923c",
  doc: "#60a5fa",
  docx: "#60a5fa",
  png: "#34d399",
  jpg: "#34d399",
  jpeg: "#34d399",
  mp4: "#38bdf8",
};

interface GraphNodeObject {
  id: string;
  name: string;
  is_folder: boolean;
  ext: string;
  depth: number;
  x?: number;
  y?: number;
}

export default function ResourceGraph({
  rootId,
  onOpenFolder,
}: {
  rootId: string;
  onOpenFolder?: (id: string, name: string) => void;
}) {
  const router = useRouter();
  const containerRef = useRef<HTMLDivElement>(null);
  const [dims, setDims] = useState({ width: 800, height: 560 });

  useEffect(() => {
    const measure = () => {
      if (containerRef.current) {
        setDims({
          width: containerRef.current.clientWidth,
          height: Math.max(440, window.innerHeight - 300),
        });
      }
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);

  const { data, isLoading } = useQuery({
    queryKey: ["catalog-graph", rootId],
    queryFn: () => getGraph(rootId, 2, true),
    staleTime: 5 * 60_000,
  });

  const handleClick = useCallback(
    (node: GraphNodeObject) => {
      if (node.is_folder) {
        if (node.id !== rootId) onOpenFolder?.(node.id, node.name);
      } else {
        // The viewer route handles missing access with a request prompt.
        router.push(`/resources/view?fileId=${node.id}`);
      }
    },
    [rootId, router, onOpenFolder]
  );

  const paintNode = useCallback(
    (node: GraphNodeObject, ctx: CanvasRenderingContext2D, scale: number) => {
      const radius = node.is_folder ? (node.depth === 0 ? 9 : 6) : 3.5;
      ctx.beginPath();
      ctx.arc(node.x!, node.y!, radius, 0, 2 * Math.PI);
      ctx.fillStyle = node.is_folder
        ? node.depth === 0
          ? "#a78bfa"
          : "#7c3aed"
        : EXT_COLORS[node.ext] ?? "#737373";
      ctx.fill();
      if (scale > 1.2 || node.is_folder) {
        ctx.font = `${node.is_folder ? 4.5 : 3.5}px sans-serif`;
        ctx.textAlign = "center";
        ctx.fillStyle = "rgba(238,238,238,0.72)";
        ctx.fillText(node.name, node.x!, node.y! + radius + 5);
      }
    },
    []
  );

  return (
    <div ref={containerRef} className="glass relative overflow-hidden">
      {data?.truncated && (
        <span className="absolute left-3 top-3 z-10 rounded-full bg-warning/10 px-3 py-1.5 text-xs text-warning">
          Large folder — showing a sample; open subfolders to see more
        </span>
      )}

      {isLoading ? (
        <div className="grid h-[440px] place-items-center text-muted">
          <Loader2 className="size-6 animate-spin" />
        </div>
      ) : (
        data && (
          <ForceGraph2D
            width={dims.width}
            height={dims.height}
            graphData={{
              nodes: data.nodes.map((n) => ({ ...n })),
              links: data.links.map((l) => ({ ...l })),
            }}
            backgroundColor="rgba(0,0,0,0)"
            nodeCanvasObject={paintNode as never}
            nodePointerAreaPaint={((node: GraphNodeObject, color: string, ctx: CanvasRenderingContext2D) => {
              ctx.beginPath();
              ctx.arc(node.x!, node.y!, 8, 0, 2 * Math.PI);
              ctx.fillStyle = color;
              ctx.fill();
            }) as never}
            linkColor={((link: { kind: string }) =>
              link.kind === "wiki"
                ? "rgba(167,139,250,0.5)"
                : "rgba(255,255,255,0.12)") as never}
            linkLineDash={((link: { kind: string }) =>
              link.kind === "wiki" ? [2, 2] : null) as never}
            linkWidth={1}
            onNodeClick={handleClick as never}
            cooldownTicks={120}
          />
        )
      )}
    </div>
  );
}
