import { LegalPage } from "@/components/legal-page";

export default function CopyrightPage() {
  return (
    <LegalPage
      title="Copyright Policy"
      updated="May 31, 2026"
      sections={[
        {
          title: "Creator responsibility",
          body: "Only upload video, audio, images, captions, or brand assets that you own, have licensed, or otherwise have the right to process and publish.",
        },
        {
          title: "Stock and music assets",
          body: "Provider assets may carry license terms, attribution requirements, platform restrictions, or availability changes. Review asset details before publishing final exports.",
        },
        {
          title: "Takedowns",
          body: "Rights holders can report allegedly infringing content with identifying details, contact information, and a statement of authorization to act on behalf of the owner.",
        },
      ]}
    />
  );
}
