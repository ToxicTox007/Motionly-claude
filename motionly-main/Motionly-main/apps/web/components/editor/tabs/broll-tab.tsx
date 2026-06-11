"use client";

import React, { useState } from "react";
import { useRouter } from "next/navigation";
import { Label, Slider } from "../ui";
import { Button } from "../../button";
import { Film, Loader2, Sparkles } from "lucide-react";
import type { BrollSlotSummary, BrollDensity } from "@motionly/shared";
import { buildApiHeaders } from "@/lib/client-api";
import { env } from "@/lib/env";

interface BrollTabProps {
  projectId: string;
  workspaceId: string;
  userId: string;
  apiAccessToken: string;
  initialSlots: BrollSlotSummary[];
  activePlanId: string | null;
  activePlanBrollDensity: BrollDensity | null;
}

export function BrollTab({
  projectId,
  workspaceId,
  userId,
  apiAccessToken,
  initialSlots,
  activePlanId,
  activePlanBrollDensity,
}: BrollTabProps) {
  const router = useRouter();
  const [density, setDensity] = useState<BrollDensity>(activePlanBrollDensity || "medium");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const densityMap: Record<BrollDensity, number> = {
    none: 0,
    low: 33,
    medium: 66,
    high: 100
  };

  const reverseDensityMap = (val: number): BrollDensity => {
    if (val < 25) return "none";
    if (val < 50) return "low";
    if (val < 75) return "medium";
    return "high";
  };

  async function regenerateBroll() {
    if (!activePlanId) return;
    setLoading(true);
    setMessage(null);
    try {
      const response = await fetch(`${env.apiBaseUrl}/projects/${projectId}/broll/generate`, {
        method: "POST",
        headers: {
          ...(await buildApiHeaders({ workspaceId, userId, accessToken: apiAccessToken })),
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          edit_plan_id: activePlanId,
          density,
          force: true,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error?.message ?? "Failed to generate B-roll");
      setMessage(`B-roll queued with ${payload.slots?.length ?? 0} slots.`);
      router.refresh();
    } catch (error) {
      console.error(error);
      setMessage((error as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="pixel-panel pixel-corners space-y-6 p-4">
        <Slider
          label="B-Roll Density"
          value={densityMap[density]}
          onChange={(val) => setDensity(reverseDensityMap(val))}
        />
        <p className="text-[10px] text-muted-foreground italic font-medium">
          {density === "none" ? "No extra media" : 
           density === "low" ? "Occasional visual support" : 
           density === "medium" ? "Standard dynamic edit" : 
           "Maximum visual variety"}
        </p>

        <div className="border-t border-border pt-6">
          <div className="flex items-center justify-between mb-4">
             <Label className="mb-0">Selected Assets</Label>
             <span className="text-xs font-bold text-muted-foreground">{initialSlots.length} Slots</span>
          </div>
          <div className="grid grid-cols-2 gap-3">
             {initialSlots.slice(0, 4).map((slot, i) => (
               <div key={slot.id} className="group relative aspect-video overflow-hidden border border-border bg-muted">
                  {slot.selected_asset?.thumbnail_url ? (
                    <img src={slot.selected_asset.thumbnail_url} alt="" className="w-full h-full object-cover" />
                  ) : (
                    <div className="w-full h-full flex items-center justify-center text-muted-foreground/30">
                       <Film className="size-4" />
                    </div>
                  )}
                  <div className="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center">
                     <button className="border border-white/30 bg-white/20 px-2 py-1 text-[8px] font-bold uppercase tracking-widest text-white backdrop-blur-md">Change</button>
                  </div>
               </div>
             ))}
             {initialSlots.length > 4 && (
               <div className="flex aspect-video items-center justify-center border border-dashed border-border text-[10px] font-bold text-muted-foreground">
                 +{initialSlots.length - 4} More
               </div>
             )}
          </div>
        </div>
      </div>

      <Button onClick={regenerateBroll} disabled={loading || !activePlanId} className="h-12 w-full">
        {loading ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Sparkles className="mr-2 size-4" />}
        Regenerate B-Roll
      </Button>
      {message ? (
        <p className="border border-border bg-muted p-3 text-xs font-bold text-muted-foreground">
          {message}
        </p>
      ) : null}
    </div>
  );
}
