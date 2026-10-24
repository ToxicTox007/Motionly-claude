import { LegalPage } from "@/components/legal-page";

export default function PrivacyPage() {
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
        {
          title: "AI service providers",
          body: "Depending on how Motionly is configured, some AI features run on Motionly's own servers and others use the service providers listed below. Motionly sends each provider only what its feature needs, over encrypted connections. For video analysis, providers receive a small number of still frames (about one per scene), never the full video.",
        },
        {
          title: "Google Cloud Vision",
          body: "Receives still frames from your video to recognise the objects in them. Google states that these requests are processed in memory, are not stored, and are not used to train its models.",
        },
        {
          title: "OpenRouter",
          body: "Receives still frames from your video to write short scene descriptions, and the speech track of your video, in short clips, to estimate the speaker's tone of voice. OpenRouter forwards each request to the company that runs the model (for example Google). Motionly only allows endpoints that neither retain the request content nor use it for training.",
        },
        {
          title: "Jina AI",
          body: "Receives still frames from your video and short text descriptions of the B-roll Motionly searches for, and returns numeric representations used to match stock footage to your video. Jina AI handles this content under its own terms of service.",
        },
        {
          title: "pyannoteAI",
          body: "Receives the speech track of your video to tell speakers apart. pyannoteAI deletes uploaded audio within 48 hours and job results after 24 hours, and does not train on customer data.",
        },
        {
          title: "LALAL.AI",
          body: "Receives the audio track of your video, without the picture, when you ask Motionly to separate the voice from the background audio. Motionly asks LALAL.AI to delete the uploaded audio as soon as the separated tracks have been downloaded.",
        },
        {
          title: "Deletion",
          body: "Workspace owners can request deletion of account data, source uploads, generated exports, and cached artifacts. Some billing and security records may be retained where required by law.",
        },
      ]}
    />
  );
}
