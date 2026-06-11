"use client";

import React, { useState } from "react";
import { Label, Slider } from "../ui";
import { Button } from "../../button";
import { Film, Loader2, Sparkles, Image as ImageIcon } from "lucide-react";
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
  const [density, setDensity] = useState<BrollDensity>(activePlanBrollDensity || "medium");
  const [loading, setLoading] = useState(false);

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
      if (!response.ok) throw new Error("Failed to generate B-roll");
      alert("B-roll regenerated!");
    } catch (error) {
      console.error(error);
      alert("Failed to regenerate B-roll.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-8 animate-in fade-in duration-300">
      <div className="space-y-6">
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

        <div className="pt-6 border-t border-border">
          <div className="flex items-center justify-between mb-4">
             <Label className="mb-0">Selected Assets</Label>
             <span className="text-xs font-bold text-muted-foreground">{initialSlots.length} Slots</span>
          </div>
          <div className="grid grid-cols-2 gap-3">
             {initialSlots.slice(0, 4).map((slot, i) => (
               <div key={slot.id} className="aspect-video rounded-lg bg-muted border border-border overflow-hidden relative group">
                  {slot.selected_asset?.thumbnail_url ? (
                    <img src={slot.selected_asset.thumbnail_url} alt="" className="w-full h-full object-cover" />
                  ) : (
                    <div className="w-full h-full flex items-center justify-center text-muted-foreground/30">
                       <Film className="size-4" />
                    </div>
                  )}
                  <div className="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center">
                     <button className="text-[8px] font-bold text-white uppercase tracking-widest bg-white/20 px-2 py-1 rounded backdrop-blur-md">Change</button>
                  </div>
               </div>
             ))}
             {initialSlots.length > 4 && (
               <div className="aspect-video rounded-lg border border-dashed border-border flex items-center justify-center text-[10px] font-bold text-muted-foreground">
                 +{initialSlots.length - 4} More
               </div>
             )}
          </div>
        </div>
      </div>

      <Button onClick={regenerateBroll} disabled={loading || !activePlanId} className="w-full h-12 rounded-xl">
        {loading ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Sparkles className="mr-2 size-4" />}
        Regenerate B-Roll
      </Button>
    </div>
  );
}
