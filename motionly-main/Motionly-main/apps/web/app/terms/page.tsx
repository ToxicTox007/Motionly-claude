import { LegalPage } from "@/components/legal-page";

export default function TermsPage() {
  return (
    <LegalPage
      title="Terms of Service"
      updated="May 31, 2026"
      sections={[
        {
          title: "Service use",
          body: "Motionly helps create short-form video exports from content you upload or authorize. You are responsible for the rights, permissions, and accuracy of source material and publishing decisions.",
        },
        {
          title: "Acceptable content",
          body: "Do not upload illegal, infringing, abusive, or privacy-violating content. We may suspend processing or accounts that create operational, legal, or platform risk.",
        },
        {
          title: "Beta availability",
          body: "During beta, features, limits, providers, and render quality may change as we harden reliability and launch operations.",
        },
      ]}
    />
  );
}
