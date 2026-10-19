import { Button } from "@/components/button";
import { Alert, Select, Textarea } from "@/components/brutal-ui";
import { PixelIcon } from "@/components/pixel-icons";
import { submitFeedback } from "./actions";
import { PageTitle } from "@/components/page-title";

export default async function FeedbackPage({
  searchParams,
}: {
  searchParams?: Promise<Record<string, string | undefined>>;
}) {
  const params = await searchParams;
  return (
    <main className="container-content flex min-h-screen flex-col justify-center py-12">
      <PageTitle title="Feedback" />
      <div className="mb-8 flex items-center gap-3">
        <div className="flex size-11 items-center justify-center rounded border-2 border-foreground bg-primary text-primary-foreground shadow-brutal-sm">
          <PixelIcon name="info" className="size-5" />
        </div>
        <div>
          <p className="text-sm font-black uppercase text-primary">
            Motionly beta
          </p>
          <h1 className="text-3xl font-black uppercase">Feedback</h1>
        </div>
      </div>
      {params?.sent ? (
        <Alert tone="success" className="mb-4 max-w-2xl">
          Feedback saved. Thank you.
        </Alert>
      ) : null}
      {params?.error ? (
        <Alert tone="danger" className="mb-4 max-w-2xl">
          Feedback could not be saved. Please try again.
        </Alert>
      ) : null}
      <form
        action={submitFeedback}
        className="max-w-2xl space-y-4 rounded border-2 border-foreground bg-surface p-5 shadow-brutal"
      >
        <input type="hidden" name="pageUrl" value="/feedback" />
        <label className="block text-sm font-semibold">
          Category
          <Select name="category" className="mt-2" defaultValue="general">
            <option value="general">General</option>
            <option value="bug">Bug</option>
            <option value="render_quality">Render quality</option>
            <option value="billing">Billing</option>
            <option value="feature_request">Feature request</option>
          </Select>
        </label>
        <label className="block text-sm font-semibold">
          Message
          <Textarea
            name="message"
            required
            minLength={10}
            rows={7}
            className="mt-2"
            placeholder="Tell us what happened, what you expected, and the project URL if relevant."
          />
        </label>
        <Button type="submit">Send feedback</Button>
      </form>
    </main>
  );
}
