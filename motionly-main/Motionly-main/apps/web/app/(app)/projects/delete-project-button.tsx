"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Trash2 } from "lucide-react";
import { Button } from "@/components/button";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { useToast } from "@/components/toast";
import { messageFor } from "@/lib/error-messages";
import { deleteProjectResult } from "./actions";

/**
 * Deletes a project from the projects list only after confirmation (it is
 * irreversible), and reports the real outcome: a failed delete keeps the row
 * and says why.
 */
export function DeleteProjectButton({
  projectId,
  title,
}: {
  projectId: string;
  title: string;
}) {
  const router = useRouter();
  const { toast } = useToast();
  const [open, setOpen] = useState(false);

  const remove = async () => {
    const result = await deleteProjectResult(projectId);
    if (!result.ok) {
      toast({
        title: "Delete failed",
        description: `Could not delete "${title}". ${messageFor("projects", result.error)} The project was kept.`,
        variant: "error",
      });
      return;
    }
    setOpen(false);
    toast({
      title: "Project deleted",
      description: `"${title}" has been deleted.`,
      variant: "success",
    });
    router.refresh();
  };

  return (
    <>
      <Button
        type="button"
        variant="secondary"
        className="text-danger"
        aria-label={`Delete ${title}`}
        aria-haspopup="dialog"
        onClick={() => setOpen(true)}
      >
        <Trash2 className="size-4" aria-hidden="true" />
      </Button>
      <ConfirmDialog
        open={open}
        title="Delete project?"
        body={`"${title}" and its captions, analyses and cached exports will be permanently deleted. This cannot be undone.`}
        confirmLabel="Delete project"
        pendingLabel="Deleting…"
        onCancel={() => setOpen(false)}
        onConfirm={remove}
      />
    </>
  );
}
