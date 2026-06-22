import { LegalPage } from "@/components/legal-page";

export default function PrivacyPage() {
  return (
    <LegalPage
      title="Privacy Policy"
      updated="May 31, 2026"
      sections={[
        {
          title: "Data we process",
          body: "Motionly processes account details, workspace activity, uploaded video files, generated transcripts, AI-populated timelines, renders, billing state, and support messages needed to provide the service.",
        },
        {
          title: "How data is used",
          body: "We use creator content to operate upload, transcription, planning, rendering, billing, safety, support, observability, and abuse-prevention workflows. We do not sell creator content.",
        },
        {
          title: "Deletion",
          body: "Workspace owners can request deletion of account data, source uploads, generated exports, and cached artifacts. Some billing and security records may be retained where required by law.",
        },
      ]}
    />
  );
}
