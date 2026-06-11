import { MessageSquare } from "lucide-react";
import { Button } from "@/components/button";

export default function FeedbackPage() {
  return (
    <main className="container-content flex min-h-screen flex-col justify-center py-12">
      <div className="mb-8 flex items-center gap-3">
        <div className="pixel-button flex size-10 items-center justify-center bg-foreground text-background">
          <MessageSquare className="size-5" aria-hidden="true" />
        </div>
        <div>
          <p className="text-sm font-bold">Motionly beta</p>
          <h1 className="text-2xl font-bold">Feedback</h1>
        </div>
      </div>
      <form
        action="mailto:support@motionly.local"
        method="post"
        encType="text/plain"
        className="pixel-panel pixel-corners max-w-2xl space-y-4 p-5"
      >
        <label className="block text-sm font-semibold">
          Category
          <select
            name="category"
            className="pixel-input mt-2 h-10 w-full px-3"
            defaultValue="general"
          >
            <option value="general">General</option>
            <option value="bug">Bug</option>
            <option value="render_quality">Render quality</option>
            <option value="billing">Billing</option>
            <option value="feature_request">Feature request</option>
          </select>
        </label>
        <label className="block text-sm font-semibold">
          Message
          <textarea
            name="message"
            required
            minLength={10}
            rows={7}
            className="pixel-input mt-2 w-full px-3 py-2"
            placeholder="Tell us what happened, what you expected, and the project URL if relevant."
          />
        </label>
        <Button type="submit">Send feedback</Button>
      </form>
    </main>
  );
}
