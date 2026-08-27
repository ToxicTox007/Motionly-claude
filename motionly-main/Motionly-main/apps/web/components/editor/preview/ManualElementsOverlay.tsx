"use client";

import Image from "next/image";
import React from "react";
import { useDraggable } from "@dnd-kit/core";
import { cn } from "@/lib/utils";
import type { ManualElement } from "@motionly/shared";

export function ManualElementsOverlay({
  elements,
  playheadMs,
  selectedId,
  onSelect,
}: {
  elements: ManualElement[];
  playheadMs: number;
  selectedId?: string | null;
  onSelect?: (id: string) => void;
}) {
  return (
    <div className="pointer-events-none absolute inset-0 z-20 overflow-hidden">
      {elements
        .filter(
          (item) =>
            item.zone === "canvas" &&
            playheadMs >= item.start_ms &&
            playheadMs < item.end_ms,
        )
        .map((item) => (
          <PlacedElement
            key={item.id}
            item={item}
            selected={item.id === selectedId}
            onSelect={onSelect}
          />
        ))}
    </div>
  );
}

function PlacedElement({
  item,
  selected,
  onSelect,
}: {
  item: ManualElement;
  selected: boolean;
  onSelect?: (id: string) => void;
}) {
  const { attributes, listeners, setNodeRef, transform, isDragging } =
    useDraggable({
      id: `placed:${item.id}`,
      data: { kind: "placed", element: item },
    });
  const properties = item.properties ?? {};
  const content = String(properties.content ?? item.name);
  const background = String(
    properties.background ??
      properties.backgroundColor ??
      (item.type === "shape" ? "#14b8a6" : "transparent"),
  );
  const color = String(properties.color ?? "#ffffff");
  const shape = String(properties.shape ?? "");
  const isCircle = shape === "circle";
  const assetUrl =
    typeof properties.assetUrl === "string" ? properties.assetUrl : null;
  return (
    <button
      ref={setNodeRef}
      type="button"
      {...attributes}
      {...listeners}
      onClick={(event) => {
        event.stopPropagation();
        onSelect?.(item.id);
      }}
      className={cn(
        "pointer-events-auto relative absolute flex touch-none items-center justify-center overflow-hidden border border-transparent px-2 text-center leading-tight",
        selected && "border-primary ring-1 ring-primary",
        isDragging && "opacity-50",
      )}
      style={{
        left: `${item.x_pct}%`,
        top: `${item.y_pct}%`,
        width: `${item.width_pct}%`,
        height: `${item.height_pct}%`,
        opacity: item.opacity,
        transform: `translate(calc(-50% + ${transform?.x ?? 0}px), calc(-50% + ${transform?.y ?? 0}px)) rotate(${item.rotation_deg}deg)`,
        background,
        color,
        borderRadius: isCircle
          ? "9999px"
          : Number(properties.borderRadius ?? 4),
        fontSize: `${Number(properties.fontSize ?? (item.type === "caption" ? 28 : 24))}px`,
        fontWeight: Number(
          properties.fontWeight ?? (item.type === "caption" ? 900 : 700),
        ),
      }}
      aria-label={`${item.name}. Drag to reposition.`}
    >
      {item.type === "graphic" && assetUrl ? (
        <Image
          src={assetUrl}
          alt={String(properties.altText ?? item.name)}
          className="object-contain"
          draggable={false}
          fill
          sizes="(max-width: 768px) 100vw, 50vw"
          unoptimized
        />
      ) : item.type === "shape" && !["arrow", "line"].includes(shape) ? null : (
        content
      )}
    </button>
  );
}
