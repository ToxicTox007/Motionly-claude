import { readFile } from "node:fs/promises";
import path from "node:path";
import { AppShell } from "@/components/app-shell";
import { Alert } from "@/components/brutal-ui";

type ReferenceRoute = {
  method: string;
  path: string;
  summary: string;
  description: string;
};

type ApiKeyReference = {
  scheme: string;
  note: string;
  routes: ReferenceRoute[];
};

// The reference is generated from packages/shared/openapi.json filtered to the
// API-key allow-list (apps/api/scripts/generate_api_key_reference.py). A test
// fails if it and the allow-list disagree, so this page is always accurate.
async function loadReference(): Promise<ApiKeyReference> {
  const file = path.join(
    process.cwd(),
    "..",
    "..",
    "packages",
    "shared",
    "api-key-reference.json",
  );
  const text = await readFile(file, "utf8");
  return JSON.parse(text) as ApiKeyReference;
}

export default async function ApiReferencePage() {
  let reference: ApiKeyReference | null = null;
  try {
    reference = await loadReference();
  } catch {
    reference = null;
  }

  return (
    <AppShell title="API Reference">
      <div className="space-y-5">
        <Alert tone="info" title="API key access">
          {reference?.note ??
            "Agency-plan API keys may call only a documented set of routes."}
        </Alert>

        <section className="rounded-neu-lg bg-surface p-5 shadow-neu-raised sm:p-6">
          <h2 className="text-lg font-bold text-foreground">Authentication</h2>
          <p className="mt-2 text-sm font-medium text-muted-foreground">
            Send your key in the <code>Authorization</code> header:
          </p>
          <pre className="mt-3 overflow-x-auto rounded-neu bg-surface p-3 text-xs shadow-neu-inset">
            <code className="font-mono text-foreground">
              Authorization: {reference?.scheme ?? "Bearer mtly_<key>"}
            </code>
          </pre>
        </section>

        <section className="rounded-neu-lg bg-surface p-5 shadow-neu-raised sm:p-6">
          <h2 className="text-lg font-bold text-foreground">
            Available endpoints
          </h2>
          {reference && reference.routes.length > 0 ? (
            <div className="mt-4 overflow-x-auto">
              <table className="w-full text-left text-sm">
                <caption className="sr-only">
                  Endpoints callable with a Motionly API key
                </caption>
                <thead>
                  <tr className="text-xs font-semibold text-muted-foreground">
                    <th scope="col" className="py-2 pr-4">
                      Method
                    </th>
                    <th scope="col" className="py-2 pr-4">
                      Path
                    </th>
                    <th scope="col" className="py-2">
                      Description
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-hairline">
                  {reference.routes.map((route) => (
                    <tr
                      key={`${route.method} ${route.path}`}
                      className="align-top"
                    >
                      <td className="py-3 pr-4 font-mono text-xs font-semibold text-primary">
                        {route.method}
                      </td>
                      <td className="py-3 pr-4 font-mono text-xs text-foreground">
                        {route.path}
                      </td>
                      <td className="py-3 text-muted-foreground">
                        {route.summary || route.description || "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="mt-3 text-sm font-medium text-muted-foreground">
              The API reference is being generated. Please check back shortly.
            </p>
          )}
        </section>
      </div>
    </AppShell>
  );
}
