import { SubmitButton } from "@/components/submit-button";
import { Alert, Select, Textarea } from "@/components/brutal-ui";
import { PixelIcon } from "@/components/pixel-icons";
import { submitFeedback } from "./actions";
import { PageTitle } from "@/components/page-title";
import { messageFor } from "@/lib/error-messages";

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
        <div className="flex size-11 items-center justify-center rounded-neu bg-primary text-primary-foreground shadow-neu-glow">
          <PixelIcon name="info" className="size-5" />
        </div>
        <div>
          <p className="text-sm font-semibold text-primary">Motionly beta</p>
          <h1 className="text-3xl font-bold tracking-tight text-foreground">
            Feedback
          </h1>
        </div>
      </div>
      {params?.sent ? (
        <Alert tone="success" className="mb-4 max-w-2xl">
          Feedback saved. Thank you.
        </Alert>
      ) : null}
      {params?.error ? (
        <Alert tone="danger" className="mb-4 max-w-2xl">
          {messageFor("feedback", params.error)}
        </Alert>
      ) : null}
      <form
        action={submitFeedback}
        className="max-w-2xl space-y-4 rounded-neu-lg bg-surface p-6 shadow-neu-raised"
      >
        <input type="hidden" name="pageUrl" value="/feedback" />
        <label className="block text-sm font-semibold text-foreground">
          Category
          <Select name="category" className="mt-2" defaultValue="general">
            <option value="general">General</option>
            <option value="bug">Bug</option>
            <option value="render_quality">Render quality</option>
            <option value="billing">Billing</option>
            <option value="feature_request">Feature request</option>
          </Select>
        </label>
        <label className="block text-sm font-semibold text-foreground">
          Message
          <Textarea
            name="message"
            required
            minLength={10}
            maxLength={5000}
            rows={7}
            className="mt-2"
            placeholder="Tell us what happened, what you expected, and the project URL if relevant."
          />
        </label>
        <SubmitButton pendingLabel="Sending…">Send feedback</SubmitButton>
      </form>
    </main>
  );
}
