"use client";

import { useState, useTransition } from "react";
import { Check, Copy, KeyRound, Trash2 } from "lucide-react";
import { Button } from "@/components/button";
import { createApiKey, revokeApiKey, type ApiKeyView } from "./actions";

function formatDate(value: string | null) {
  if (!value) return "Never";
  return new Date(value).toLocaleString();
}

// Shows a newly-created key's raw secret exactly once, with a copy button. The
// secret lives only in this component's state; reloading the page loses it.
function RevealedSecret({ secret }: { secret: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(secret);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };
  return (
    <div
      className="rounded-neu-lg bg-surface p-4 shadow-neu-raised"
      role="status"
      aria-live="polite"
    >
      <p className="text-sm font-semibold text-primary">
        Copy your new API key now
      </p>
      <p className="mt-1 text-xs font-medium text-muted-foreground">
        This is the only time the full key is shown. Store it somewhere safe —
        you will not be able to see it again.
      </p>
      <div className="mt-3 flex items-center gap-2">
        <code
          className="flex-1 overflow-x-auto rounded-neu bg-surface px-3 py-2 font-mono text-xs text-foreground shadow-neu-inset"
          data-testid="api-key-secret"
        >
          {secret}
        </code>
        <Button
          type="button"
          size="sm"
          onClick={copy}
          aria-label="Copy API key"
        >
          {copied ? <Check className="size-4" /> : <Copy className="size-4" />}
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
    </div>
  );
}

export function ApiKeysClient({ initialKeys }: { initialKeys: ApiKeyView[] }) {
  const [keys, setKeys] = useState<ApiKeyView[]>(initialKeys);
  const [name, setName] = useState("");
  const [secret, setSecret] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  const onCreate = (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    setSecret(null);
    startTransition(async () => {
      const result = await createApiKey(name);
      if (!result.ok) {
        setError(result.error.replaceAll("_", " "));
        return;
      }
      setSecret(result.secret);
      setKeys((current) => [result.key, ...current]);
      setName("");
    });
  };

  const onRevoke = (keyId: string) => {
    setError(null);
    startTransition(async () => {
      const result = await revokeApiKey(keyId);
      if (!result.ok) {
        setError(result.error.replaceAll("_", " "));
        return;
      }
      setKeys((current) =>
        current.map((key) =>
          key.id === keyId
            ? { ...key, revoked_at: new Date().toISOString() }
            : key,
        ),
      );
    });
  };

  return (
    <div className="space-y-5">
      <form
        onSubmit={onCreate}
        className="rounded-neu-lg bg-surface p-5 shadow-neu-raised sm:p-6"
      >
        <label
          htmlFor="api-key-name"
          className="text-sm font-semibold text-foreground"
        >
          Create API key
        </label>
        <p className="mt-1 text-xs font-medium text-muted-foreground">
          Give the key a name so you can recognise it later (for example, the
          name of the service that will use it).
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <input
            id="api-key-name"
            name="name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength={120}
            required
            placeholder="CI pipeline"
            className="min-h-11 min-w-0 flex-1 rounded-neu border border-control bg-surface px-4 py-2 text-sm font-medium text-foreground shadow-neu-inset-sm focus:border-focus focus:outline-none focus:ring-4 focus:ring-primary/30"
          />
          <Button type="submit" disabled={pending || !name.trim()}>
            <KeyRound className="size-4" aria-hidden="true" />
            {pending ? "Creating…" : "Create key"}
          </Button>
        </div>
        {error ? (
          <p className="mt-3 text-sm font-semibold text-danger" role="alert">
            {error}
          </p>
        ) : null}
      </form>

      {secret ? <RevealedSecret secret={secret} /> : null}

      <section
        aria-labelledby="api-keys-heading"
        className="rounded-neu-lg bg-surface p-5 shadow-neu-raised sm:p-6"
      >
        <h2 id="api-keys-heading" className="text-lg font-bold text-foreground">
          Your API keys
        </h2>
        {keys.length === 0 ? (
          <p className="mt-3 text-sm font-medium text-muted-foreground">
            No API keys yet. Create one above to start using the Motionly API.
          </p>
        ) : (
          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Workspace API keys</caption>
              <thead>
                <tr className="text-xs font-semibold text-muted-foreground">
                  <th scope="col" className="py-2 pr-4">
                    Name
                  </th>
                  <th scope="col" className="py-2 pr-4">
                    Key
                  </th>
                  <th scope="col" className="py-2 pr-4">
                    Last used
                  </th>
                  <th scope="col" className="py-2 pr-4">
                    Status
                  </th>
                  <th scope="col" className="py-2">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-hairline">
                {keys.map((key) => {
                  const revoked = Boolean(key.revoked_at);
                  return (
                    <tr key={key.id}>
                      <td className="py-3 pr-4 font-semibold text-foreground">
                        {key.name}
                      </td>
                      <td className="py-3 pr-4 font-mono text-xs text-muted-foreground">
                        {key.prefix}…{key.last4}
                      </td>
                      <td className="py-3 pr-4 text-muted-foreground">
                        {formatDate(key.last_used_at)}
                      </td>
                      <td className="py-3 pr-4">
                        {revoked ? (
                          <span className="inline-flex items-center gap-1.5 rounded-full bg-muted px-2.5 py-0.5 text-xs font-semibold text-warning shadow-neu-inset-sm">
                            Revoked
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1.5 rounded-full bg-muted px-2.5 py-0.5 text-xs font-semibold text-success shadow-neu-inset-sm">
                            Active
                          </span>
                        )}
                      </td>
                      <td className="py-3">
                        {revoked ? null : (
                          <Button
                            type="button"
                            variant="secondary"
                            size="sm"
                            onClick={() => onRevoke(key.id)}
                            disabled={pending}
                            aria-label={`Revoke API key ${key.name}`}
                          >
                            <Trash2 className="size-4" aria-hidden="true" />
                            Revoke
                          </Button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
