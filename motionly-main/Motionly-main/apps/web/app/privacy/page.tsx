import { LegalPage } from "@/components/legal-page";
import { fetchDataProcessors } from "@/lib/data-processors";

// Server component: the processor sections are rendered from
// GET /public/data-processors (plan Task 9A), never hard-coded. If the API is
// unreachable the helper returns an empty list and the page shows a neutral
// fallback message instead of crashing.
export default async function PrivacyPage() {
  const processors = await fetchDataProcessors();

  const processorSection =
    processors.length > 0
      ? {
          title: "AI service providers",
          body: (
            <>
              <p className="mb-4">
                Depending on how Motionly is configured, some AI features run on
                Motionly&apos;s own servers and others use the service providers
                listed below. Motionly sends each provider only what its feature
                needs, over encrypted connections.
              </p>
              <ul className="space-y-5">
                {processors.map((processor) => (
                  <li key={`${processor.display_name}|${processor.policy_url}`}>
                    <h3 className="text-base font-bold text-foreground">
                      {processor.display_name}
                    </h3>
                    <p className="mt-1">{processor.purpose}</p>
                    {processor.data_categories.length > 0 ? (
                      <p className="mt-1">
                        Data received: {processor.data_categories.join(", ")}.
                      </p>
                    ) : null}
                    <p className="mt-1">
                      <a
                        href={processor.policy_url}
                        className="font-semibold text-foreground underline underline-offset-2"
                        target="_blank"
                        rel="noreferrer noopener"
                      >
                        {processor.display_name} privacy policy
                      </a>
                    </p>
                  </li>
                ))}
              </ul>
            </>
          ),
        }
      : {
          title: "AI service providers",
          body: "Depending on how Motionly is configured, some AI features run on Motionly's own servers and others use external service providers. The current list of providers is temporarily unavailable; please check back shortly. Motionly sends each provider only what its feature needs, over encrypted connections, and does not sell creator content.",
        };

  return (
    <LegalPage
      title="Privacy Policy"
      updated="October 23, 2026"
      sections={[
        {
          title: "Data we process",
          body: "Motionly processes account details, workspace activity, uploaded video files, generated transcripts, AI-populated timelines, renders, billing state, and support messages needed to provide the service.",
        },
        {
          title: "How data is used",
          body: "We use creator content to operate upload, transcription, planning, rendering, billing, safety, support, observability, and abuse-prevention workflows. We do not sell creator content.",
        },
        processorSection,
        {
          title: "Deletion",
          body: "Workspace owners can request deletion of account data, source uploads, generated exports, and cached artifacts. Some billing and security records may be retained where required by law.",
        },
      ]}
    />
  );
}
