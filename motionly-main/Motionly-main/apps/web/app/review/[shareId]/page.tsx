import { PublicReviewClient } from "./PublicReviewClient";

export default async function PublicReviewPage({
  params,
}: {
  params: Promise<{ shareId: string }>;
}) {
  const { shareId } = await params;
  return (
    <div className="min-h-screen bg-background text-foreground">
      <PublicReviewClient shareId={shareId} />
    </div>
  );
}
