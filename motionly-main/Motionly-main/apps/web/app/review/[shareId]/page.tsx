import { PublicReviewClient } from "./PublicReviewClient";
import { PageTitle } from "@/components/page-title";

export default async function PublicReviewPage({
  params,
}: {
  params: Promise<{ shareId: string }>;
}) {
  const { shareId } = await params;
  return (
    <div className="min-h-screen bg-background text-foreground">
      <PageTitle title="Revision review" />
      <PublicReviewClient shareId={shareId} />
    </div>
  );
}
