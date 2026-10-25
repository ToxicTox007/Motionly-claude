"use client";

import { useState, useTransition } from "react";
import { AlertTriangle, ArrowDown, ArrowUp, Info } from "lucide-react";
import { useFocusTrap } from "@/lib/useFocusTrap";
import {
  modelWarningBadges,
  routeHealthBadges,
  routeWarningBadges,
  sortRoutesByPriority,
  type RegistryEntry,
  type RegistrySnapshot,
  type RouteHealth,
  type RouteHealthEntry,
  type RouteTestResult,
  type SecretMetadata,
  type WarningBadge,
} from "@/lib/admin-inference";
import {
  createSecret,
  deleteEntry,
  deleteSecret,
  rotateSecret,
  saveAdapter,
  saveModel,
  saveRoute,
  testRoute,
  type RouteTestActionState,
} from "./actions";

type MutationAction = (formData: FormData) => Promise<{
  ok: boolean;
  message: string;
  warnings?: string[];
  errors?: string[];
}>;

const KIND_LABEL = {
  adapters: "adapter",
  models: "model",
  routes: "route",
} as const;

type Kind = keyof typeof KIND_LABEL;

const badgeToneClass: Record<WarningBadge["tone"], string> = {
  warning: "text-warning-foreground bg-warning",
  danger: "text-danger-foreground bg-danger",
  info: "text-ink bg-info",
};

function BadgeRow({ badges }: { badges: WarningBadge[] }) {
  if (!badges.length) return null;
  return (
    <ul className="mt-2 flex flex-wrap gap-1.5" aria-label="Warnings">
      {badges.map((badge) => (
        <li key={badge.code}>
          <span
            className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold shadow-neu-inset-sm ${badgeToneClass[badge.tone]}`}
          >
            {badge.tone === "info" ? (
              <Info className="size-3" aria-hidden="true" />
            ) : (
              <AlertTriangle className="size-3" aria-hidden="true" />
            )}
            {badge.label}
          </span>
        </li>
      ))}
    </ul>
  );
}

function StatusLine({
  state,
}: {
  state: {
    ok: boolean;
    message: string;
    warnings?: string[];
    errors?: string[];
  } | null;
}) {
  if (!state) return null;
  return (
    <div aria-live="polite" role="status" className="mt-2">
      <p
        className={`rounded-neu px-3 py-2 text-sm font-semibold shadow-neu-inset-sm ${state.ok ? "text-foreground" : "text-danger"}`}
        role={state.ok ? undefined : "alert"}
      >
        {state.message}
      </p>
      {(state.errors ?? []).length ? (
        <ul className="mt-1 list-disc pl-5 text-xs font-medium text-danger">
          {state.errors!.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      ) : null}
      {(state.warnings ?? []).length ? (
        <ul className="mt-1 list-disc pl-5 text-xs font-medium text-warning-foreground">
          {state.warnings!.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

// A JSON record editor for one adapter/model/route. File-managed entries are
// read-only; database entries can be edited or deleted (with confirmation).
function RecordForm({
  kind,
  entry,
  action,
  badges,
}: {
  kind: Kind;
  entry?: RegistryEntry;
  action: MutationAction;
  badges?: WarningBadge[];
}) {
  const readOnly = entry?.managed === "file";
  const [text, setText] = useState(
    entry ? JSON.stringify(entry.record, null, 2) : "",
  );
  const [state, setState] = useState<Awaited<
    ReturnType<MutationAction>
  > | null>(null);
  const [pending, startTransition] = useTransition();
  const id = entry?.id ?? "new";
  const fieldId = `${kind}-${id}-record`;

  const onSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formData = new FormData();
    formData.set("record", text);
    if (entry) formData.set("existingId", entry.id);
    startTransition(async () => setState(await action(formData)));
  };

  return (
    <form
      onSubmit={onSubmit}
      className="rounded-neu-md bg-surface p-4 shadow-neu-raised-sm"
    >
      <div className="mb-2 flex items-center justify-between gap-2">
        <label
          htmlFor={fieldId}
          className="text-sm font-semibold text-foreground"
        >
          {entry ? entry.id : `New ${KIND_LABEL[kind]}`}
        </label>
        {entry ? (
          <span className="inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-[11px] font-semibold text-muted-foreground shadow-neu-inset-sm">
            {readOnly ? "File · read-only" : "Database"}
          </span>
        ) : null}
      </div>
      {badges ? <BadgeRow badges={badges} /> : null}
      <textarea
        id={fieldId}
        name="record"
        value={text}
        onChange={(event) => setText(event.target.value)}
        readOnly={readOnly}
        rows={entry ? 6 : 8}
        spellCheck={false}
        aria-describedby={`${fieldId}-hint`}
        placeholder={`{\n  "id": "...",\n  ...\n}`}
        className="mt-2 w-full rounded-neu border border-control bg-surface p-3 font-mono text-xs text-foreground shadow-neu-inset-sm focus:border-focus focus:outline-none focus:ring-4 focus:ring-primary/30 read-only:opacity-70"
      />
      <p id={`${fieldId}-hint`} className="mt-1 text-xs text-muted-foreground">
        {readOnly
          ? "Managed by the file registry. Edit the registry file to change it."
          : "A JSON object with an id. Saved changes are validated before they commit."}
      </p>
      {!readOnly ? (
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            type="submit"
            disabled={pending}
            className="inline-flex min-h-10 items-center rounded-neu bg-primary px-4 text-sm font-semibold text-primary-foreground shadow-neu-glow transition-all duration-200 hover:brightness-105 active:shadow-neu-pressed disabled:opacity-50"
          >
            {pending
              ? "Saving…"
              : entry
                ? "Save changes"
                : `Add ${KIND_LABEL[kind]}`}
          </button>
          {entry ? <DeleteButton kind={kind} id={entry.id} /> : null}
        </div>
      ) : null}
      <StatusLine state={state} />
    </form>
  );
}

function DeleteButton({ kind, id }: { kind: Kind; id: string }) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<Awaited<
    ReturnType<MutationAction>
  > | null>(null);
  const [pending, startTransition] = useTransition();

  const onConfirm = () => {
    const formData = new FormData();
    formData.set("kind", kind);
    formData.set("id", id);
    startTransition(async () => {
      setState(await deleteEntry(formData));
      setOpen(false);
    });
  };

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex min-h-10 items-center rounded-neu bg-surface px-4 text-sm font-semibold text-danger shadow-neu-raised transition-all duration-200 hover:shadow-neu-raised-lg active:shadow-neu-pressed"
      >
        Delete
      </button>
      {open ? (
        <ConfirmDialog
          title={`Delete ${KIND_LABEL[kind]}`}
          body={`Delete "${id}"? This removes it from the database registry after a validation check.`}
          confirmLabel="Confirm delete"
          pending={pending}
          onConfirm={onConfirm}
          onClose={() => setOpen(false)}
        />
      ) : null}
      <StatusLine state={state} />
    </>
  );
}

function ConfirmDialog({
  title,
  body,
  confirmLabel,
  pending,
  onConfirm,
  onClose,
}: {
  title: string;
  body: string;
  confirmLabel: string;
  pending: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const ref = useFocusTrap(true, onClose);
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-background/60 p-4 backdrop-blur-sm"
      role="presentation"
    >
      <div
        ref={ref}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        aria-describedby="confirm-body"
        className="w-full max-w-md rounded-neu-xl bg-surface p-6 shadow-neu-raised-lg"
      >
        <h2 id="confirm-title" className="text-lg font-bold text-danger">
          {title}
        </h2>
        <p
          id="confirm-body"
          className="mt-2 text-sm font-medium text-muted-foreground leading-relaxed"
        >
          {body}
        </p>
        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={pending}
            className="inline-flex min-h-11 items-center rounded-neu bg-surface px-5 text-sm font-semibold text-foreground shadow-neu-raised transition-all duration-200 hover:shadow-neu-raised-lg active:shadow-neu-pressed disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={pending}
            className="inline-flex min-h-11 items-center rounded-neu bg-danger px-5 text-sm font-semibold text-danger-foreground shadow-neu-raised transition-all duration-200 hover:brightness-105 active:shadow-neu-pressed disabled:opacity-50"
          >
            {pending ? "Working…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

// Priority/fallback ordering list. Reordering is keyboard-usable: each row has
// move-up / move-down buttons; the displayed priority is advisory and the
// operator saves the new priority through the record form above.
function RoutePriorityList({ routes }: { routes: RegistryEntry[] }) {
  const [order, setOrder] = useState(() =>
    sortRoutesByPriority(routes).map((r) => r.id),
  );
  const byId = new Map(routes.map((r) => [r.id, r]));

  const move = (index: number, delta: number) => {
    setOrder((current) => {
      const next = [...current];
      const target = index + delta;
      if (target < 0 || target >= next.length) return current;
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  };

  return (
    <ol className="grid gap-2" aria-label="Route priority and fallback order">
      {order.map((routeId, index) => {
        const entry = byId.get(routeId);
        if (!entry) return null;
        const capability = String(entry.record.capability ?? "—");
        return (
          <li
            key={routeId}
            className="flex items-center justify-between gap-3 rounded-neu-md bg-surface p-3 shadow-neu-raised-sm"
          >
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold text-foreground">
                <span className="tabular-nums text-muted-foreground">
                  {index + 1}.
                </span>{" "}
                {routeId}
              </p>
              <p className="text-xs text-muted-foreground">
                {capability}
                {entry.record.enabled === false ? " · disabled" : ""}
              </p>
            </div>
            <div className="flex gap-1">
              <button
                type="button"
                onClick={() => move(index, -1)}
                disabled={index === 0}
                aria-label={`Move ${routeId} earlier in priority`}
                className="inline-flex size-9 items-center justify-center rounded-neu bg-surface text-foreground shadow-neu-raised transition-all duration-200 hover:shadow-neu-raised-lg active:shadow-neu-pressed disabled:opacity-40"
              >
                <ArrowUp className="size-4" aria-hidden="true" />
              </button>
              <button
                type="button"
                onClick={() => move(index, 1)}
                disabled={index === order.length - 1}
                aria-label={`Move ${routeId} later in priority`}
                className="inline-flex size-9 items-center justify-center rounded-neu bg-surface text-foreground shadow-neu-raised transition-all duration-200 hover:shadow-neu-raised-lg active:shadow-neu-pressed disabled:opacity-40"
              >
                <ArrowDown className="size-4" aria-hidden="true" />
              </button>
            </div>
          </li>
        );
      })}
      {order.length === 0 ? (
        <li className="rounded-neu bg-surface p-4 text-center text-sm text-muted-foreground shadow-neu-inset">
          No routes configured.
        </li>
      ) : null}
    </ol>
  );
}

function RouteTester({ routeIds }: { routeIds: string[] }) {
  const [routeId, setRouteId] = useState(routeIds[0] ?? "");
  const [allowPaid, setAllowPaid] = useState(false);
  const [maxCost, setMaxCost] = useState("0.50");
  const [state, setState] = useState<RouteTestActionState | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [pending, startTransition] = useTransition();

  const run = () => {
    const formData = new FormData();
    formData.set("routeId", routeId);
    formData.set("allowPaid", allowPaid ? "true" : "false");
    formData.set("maxCostUsd", maxCost);
    startTransition(async () => {
      setState(await testRoute(formData));
      setConfirmOpen(false);
    });
  };

  const onSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    // A paid async test must be confirmed explicitly before it is sent.
    if (allowPaid) {
      setConfirmOpen(true);
      return;
    }
    run();
  };

  return (
    <form
      onSubmit={onSubmit}
      className="rounded-neu-md bg-surface p-4 shadow-neu-raised-sm"
    >
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-[14rem] flex-1">
          <label
            htmlFor="route-test-id"
            className="block text-xs font-semibold text-muted-foreground"
          >
            Route
          </label>
          <select
            id="route-test-id"
            value={routeId}
            onChange={(event) => setRouteId(event.target.value)}
            className="mt-1.5 min-h-11 w-full rounded-neu border border-control bg-surface px-3 text-sm font-medium text-foreground shadow-neu-inset-sm focus:border-focus focus:outline-none focus:ring-4 focus:ring-primary/30"
          >
            {routeIds.map((id) => (
              <option key={id} value={id}>
                {id}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label
            htmlFor="route-test-cost"
            className="block text-xs font-semibold text-muted-foreground"
          >
            Max cost (USD)
          </label>
          <input
            id="route-test-cost"
            type="number"
            min={0}
            max={100}
            step="0.01"
            value={maxCost}
            onChange={(event) => setMaxCost(event.target.value)}
            className="mt-1.5 min-h-11 w-28 rounded-neu border border-control bg-surface px-3 text-sm font-medium tabular-nums text-foreground shadow-neu-inset-sm focus:border-focus focus:outline-none focus:ring-4 focus:ring-primary/30"
          />
        </div>
        <label className="flex items-center gap-2 text-sm font-medium text-foreground">
          <input
            type="checkbox"
            checked={allowPaid}
            onChange={(event) => setAllowPaid(event.target.checked)}
            className="size-4 rounded border-control shadow-neu-inset-sm"
          />
          Allow paid async test
        </label>
        <button
          type="submit"
          disabled={pending || !routeId}
          className="inline-flex min-h-11 items-center rounded-neu bg-primary px-5 text-sm font-semibold text-primary-foreground shadow-neu-glow transition-all duration-200 hover:brightness-105 active:shadow-neu-pressed disabled:opacity-50"
        >
          {pending ? "Testing…" : "Run test"}
        </button>
      </div>
      {state?.result ? <RouteTestResultView result={state.result} /> : null}
      <StatusLine state={state} />
      {confirmOpen ? (
        <ConfirmDialog
          title="Run a paid route test"
          body={`This will submit a paid async test against "${routeId}" with a ceiling of $${maxCost}. It counts against the operator budget scope.`}
          confirmLabel="Confirm paid test"
          pending={pending}
          onConfirm={run}
          onClose={() => setConfirmOpen(false)}
        />
      ) : null}
    </form>
  );
}

function RouteTestResultView({ result }: { result: RouteTestResult }) {
  return (
    <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-3">
      <div>
        <dt className="font-semibold text-muted-foreground">Status</dt>
        <dd className="text-foreground">{result.status}</dd>
      </div>
      <div>
        <dt className="font-semibold text-muted-foreground">Schema valid</dt>
        <dd className="text-foreground">
          {result.schema_valid ? "yes" : "no"}
        </dd>
      </div>
      <div>
        <dt className="font-semibold text-muted-foreground">Latency</dt>
        <dd className="tabular-nums text-foreground">
          {result.latency_ms ?? "—"} ms
        </dd>
      </div>
      <div>
        <dt className="font-semibold text-muted-foreground">Est. cost</dt>
        <dd className="tabular-nums text-foreground">
          {result.cost_unknown
            ? "unknown"
            : `$${result.estimated_cost_usd.toFixed(4)}`}
        </dd>
      </div>
      <div>
        <dt className="font-semibold text-muted-foreground">Circuit</dt>
        <dd className="text-foreground">{result.circuit_state}</dd>
      </div>
      <div>
        <dt className="font-semibold text-muted-foreground">Error</dt>
        <dd className="text-foreground">{result.error_code ?? "none"}</dd>
      </div>
    </dl>
  );
}

function SecretForms({ secrets }: { secrets: SecretMetadata[] }) {
  const [createState, setCreateState] = useState<Awaited<
    ReturnType<typeof createSecret>
  > | null>(null);
  const [name, setName] = useState("");
  const [scope, setScope] = useState("global");
  const [value, setValue] = useState("");
  const [pending, startTransition] = useTransition();

  const onCreate = (event: React.FormEvent) => {
    event.preventDefault();
    const formData = new FormData();
    formData.set("name", name);
    formData.set("scope", scope);
    formData.set("value", value);
    startTransition(async () => {
      const result = await createSecret(formData);
      setCreateState(result);
      // Never retain the submitted value in client state after submit.
      setValue("");
    });
  };

  return (
    <div className="grid gap-4">
      <form
        onSubmit={onCreate}
        className="rounded-neu-md bg-surface p-4 shadow-neu-raised-sm"
      >
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-[12rem] flex-1">
            <label
              htmlFor="secret-name"
              className="block text-xs font-semibold text-muted-foreground"
            >
              Secret name
            </label>
            <input
              id="secret-name"
              type="text"
              value={name}
              onChange={(event) => setName(event.target.value)}
              className="mt-1.5 min-h-11 w-full rounded-neu border border-control bg-surface px-3 text-sm font-medium text-foreground shadow-neu-inset-sm focus:border-focus focus:outline-none focus:ring-4 focus:ring-primary/30"
            />
          </div>
          <div>
            <label
              htmlFor="secret-scope"
              className="block text-xs font-semibold text-muted-foreground"
            >
              Scope
            </label>
            <input
              id="secret-scope"
              type="text"
              value={scope}
              onChange={(event) => setScope(event.target.value)}
              className="mt-1.5 min-h-11 w-32 rounded-neu border border-control bg-surface px-3 text-sm font-medium text-foreground shadow-neu-inset-sm focus:border-focus focus:outline-none focus:ring-4 focus:ring-primary/30"
            />
          </div>
          <div className="min-w-[12rem] flex-1">
            <label
              htmlFor="secret-value"
              className="block text-xs font-semibold text-muted-foreground"
            >
              Value (write-only)
            </label>
            <input
              id="secret-value"
              type="password"
              autoComplete="off"
              value={value}
              onChange={(event) => setValue(event.target.value)}
              aria-describedby="secret-value-hint"
              className="mt-1.5 min-h-11 w-full rounded-neu border border-control bg-surface px-3 text-sm font-medium text-foreground shadow-neu-inset-sm focus:border-focus focus:outline-none focus:ring-4 focus:ring-primary/30"
            />
            <span id="secret-value-hint" className="sr-only">
              The value is sent once and is never stored in the browser or shown
              again.
            </span>
          </div>
          <button
            type="submit"
            disabled={pending}
            className="inline-flex min-h-11 items-center rounded-neu bg-primary px-5 text-sm font-semibold text-primary-foreground shadow-neu-glow transition-all duration-200 hover:brightness-105 active:shadow-neu-pressed disabled:opacity-50"
          >
            {pending ? "Storing…" : "Store secret"}
          </button>
        </div>
        <StatusLine state={createState} />
      </form>

      <div className="overflow-x-auto rounded-neu-lg bg-surface shadow-neu-raised">
        <table className="min-w-[640px] w-full text-left text-sm">
          <caption className="sr-only">
            Stored secrets. Only metadata is shown — a value is never returned.
          </caption>
          <thead className="text-xs font-semibold text-muted-foreground">
            <tr className="border-b border-hairline">
              <th className="px-4 py-3">Name</th>
              <th className="px-4 py-3">Scope</th>
              <th className="px-4 py-3">Fingerprint</th>
              <th className="px-4 py-3">Key version</th>
              <th className="px-4 py-3">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-hairline">
            {secrets.map((secret) => (
              <SecretRow
                key={`${secret.scope}:${secret.name}`}
                secret={secret}
              />
            ))}
            {secrets.length === 0 ? (
              <tr>
                <td
                  className="px-4 py-8 text-center text-muted-foreground"
                  colSpan={5}
                >
                  No secrets stored.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function SecretRow({ secret }: { secret: SecretMetadata }) {
  const [rotateOpen, setRotateOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [newValue, setNewValue] = useState("");
  const [state, setState] = useState<Awaited<
    ReturnType<typeof rotateSecret>
  > | null>(null);
  const [pending, startTransition] = useTransition();

  const doRotate = () => {
    const formData = new FormData();
    formData.set("name", secret.name);
    formData.set("scope", secret.scope);
    formData.set("value", newValue);
    startTransition(async () => {
      setState(await rotateSecret(formData));
      setNewValue("");
      setRotateOpen(false);
    });
  };

  const doDelete = () => {
    const formData = new FormData();
    formData.set("name", secret.name);
    formData.set("scope", secret.scope);
    startTransition(async () => {
      setState(await deleteSecret(formData));
      setDeleteOpen(false);
    });
  };

  return (
    <tr>
      <td className="px-4 py-3 font-semibold text-foreground">{secret.name}</td>
      <td className="px-4 py-3">{secret.scope}</td>
      <td className="px-4 py-3 font-mono text-xs text-muted-foreground">
        {secret.fingerprint}
      </td>
      <td className="px-4 py-3 tabular-nums">{secret.key_version}</td>
      <td className="px-4 py-3">
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => setRotateOpen(true)}
            className="inline-flex min-h-9 items-center rounded-neu bg-surface px-3 text-xs font-semibold text-foreground shadow-neu-raised transition-all duration-200 hover:shadow-neu-raised-lg active:shadow-neu-pressed"
          >
            Rotate
          </button>
          <button
            type="button"
            onClick={() => setDeleteOpen(true)}
            className="inline-flex min-h-9 items-center rounded-neu bg-surface px-3 text-xs font-semibold text-danger shadow-neu-raised transition-all duration-200 hover:shadow-neu-raised-lg active:shadow-neu-pressed"
          >
            Delete
          </button>
        </div>
        {rotateOpen ? (
          <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-background/60 p-4 backdrop-blur-sm"
            role="presentation"
          >
            <div
              role="dialog"
              aria-modal="true"
              aria-label={`Rotate secret ${secret.name}`}
              className="w-full max-w-md rounded-neu-xl bg-surface p-6 shadow-neu-raised-lg"
            >
              <h2 className="text-lg font-bold text-foreground">
                Rotate {secret.name}
              </h2>
              <label
                htmlFor={`rotate-${secret.name}`}
                className="mt-3 block text-xs font-semibold text-muted-foreground"
              >
                New value (write-only)
              </label>
              <input
                id={`rotate-${secret.name}`}
                type="password"
                autoComplete="off"
                value={newValue}
                onChange={(event) => setNewValue(event.target.value)}
                className="mt-1.5 min-h-11 w-full rounded-neu border border-control bg-surface px-3 text-sm font-medium text-foreground shadow-neu-inset-sm focus:border-focus focus:outline-none focus:ring-4 focus:ring-primary/30"
              />
              <div className="mt-5 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setRotateOpen(false)}
                  disabled={pending}
                  className="inline-flex min-h-11 items-center rounded-neu bg-surface px-5 text-sm font-semibold text-foreground shadow-neu-raised transition-all duration-200 hover:shadow-neu-raised-lg active:shadow-neu-pressed disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={doRotate}
                  disabled={pending || !newValue}
                  className="inline-flex min-h-11 items-center rounded-neu bg-primary px-5 text-sm font-semibold text-primary-foreground shadow-neu-glow transition-all duration-200 hover:brightness-105 active:shadow-neu-pressed disabled:opacity-50"
                >
                  {pending ? "Working…" : "Rotate"}
                </button>
              </div>
            </div>
          </div>
        ) : null}
        {deleteOpen ? (
          <ConfirmDialog
            title="Delete secret"
            body={`Delete secret "${secret.name}" (${secret.scope})? Routes that reference it will fail until a new value is stored.`}
            confirmLabel="Confirm delete"
            pending={pending}
            onConfirm={doDelete}
            onClose={() => setDeleteOpen(false)}
          />
        ) : null}
        <StatusLine state={state} />
      </td>
    </tr>
  );
}

function CapabilityMatrix({ routes }: { routes: RegistryEntry[] }) {
  return (
    <div className="overflow-x-auto rounded-neu-lg bg-surface shadow-neu-raised">
      <table className="min-w-[640px] w-full text-left text-sm">
        <caption className="sr-only">
          Capability matrix of configured routes.
        </caption>
        <thead className="text-xs font-semibold text-muted-foreground">
          <tr className="border-b border-hairline">
            <th className="px-4 py-3">Route</th>
            <th className="px-4 py-3">Capability</th>
            <th className="px-4 py-3">Model</th>
            <th className="px-4 py-3">Enabled</th>
            <th className="px-4 py-3">Managed</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-hairline">
          {routes.map((entry) => (
            <tr key={entry.id}>
              <td className="px-4 py-3 font-semibold text-foreground">
                {entry.id}
              </td>
              <td className="px-4 py-3">
                {String(entry.record.capability ?? "—")}
              </td>
              <td className="px-4 py-3">{String(entry.record.model ?? "—")}</td>
              <td className="px-4 py-3">
                {entry.record.enabled === false ? "No" : "Yes"}
              </td>
              <td className="px-4 py-3">{entry.managed}</td>
            </tr>
          ))}
          {routes.length === 0 ? (
            <tr>
              <td
                className="px-4 py-8 text-center text-muted-foreground"
                colSpan={5}
              >
                No routes configured.
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </div>
  );
}

function HealthPanel({ health }: { health: RouteHealthEntry[] }) {
  return (
    <div className="grid gap-3">
      {health.map((entry) => (
        <div
          key={entry.route_id}
          className="rounded-neu-md bg-surface p-4 shadow-neu-raised-sm"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-sm font-semibold text-foreground">
              {entry.route_id}
            </p>
            <span className="inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-[11px] font-semibold text-muted-foreground shadow-neu-inset-sm">
              Circuit: {entry.circuit_state}
            </span>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {entry.capability ?? "—"} · p95 {entry.p95_latency_ms ?? "—"} ms ·
            errors{" "}
            {entry.error_rate == null
              ? "—"
              : `${Math.round(entry.error_rate * 100)}%`}{" "}
            · calls {entry.total_calls} · fallbacks {entry.fallback_count}
          </p>
          <BadgeRow badges={routeHealthBadges(entry)} />
        </div>
      ))}
      {health.length === 0 ? (
        <div className="rounded-neu-lg bg-surface p-8 text-center text-sm font-medium text-muted-foreground shadow-neu-inset">
          No route health reported.
        </div>
      ) : null}
    </div>
  );
}

export function AdminAiClient({
  snapshot,
  health,
  secrets,
}: {
  snapshot: RegistrySnapshot;
  health: RouteHealth | null;
  secrets: SecretMetadata[];
}) {
  const routeIds = snapshot.routes.map((r) => r.id);
  return (
    <div className="grid gap-10">
      <section aria-labelledby="matrix-heading">
        <h2 id="matrix-heading" className="mb-3 text-base font-bold">
          Capability matrix
        </h2>
        <p className="mb-3 text-sm text-muted-foreground">
          Registry version {snapshot.registry_version}. Changes activate after a
          validated save bumps this counter.
        </p>
        <CapabilityMatrix routes={snapshot.routes} />
      </section>

      <section aria-labelledby="health-heading">
        <h2 id="health-heading" className="mb-3 text-base font-bold">
          Route health and circuits
        </h2>
        <HealthPanel health={health?.routes ?? []} />
      </section>

      <section aria-labelledby="test-heading">
        <h2 id="test-heading" className="mb-3 text-base font-bold">
          Route test
        </h2>
        <RouteTester routeIds={routeIds} />
      </section>

      <section aria-labelledby="priority-heading">
        <h2 id="priority-heading" className="mb-3 text-base font-bold">
          Route priority and fallback order
        </h2>
        <RoutePriorityList routes={snapshot.routes} />
      </section>

      <section aria-labelledby="routes-heading">
        <h2 id="routes-heading" className="mb-3 text-base font-bold">
          Routes
        </h2>
        <div className="grid gap-3">
          <RecordForm kind="routes" action={saveRoute} />
          {snapshot.routes.map((entry) => (
            <RecordForm
              key={entry.id}
              kind="routes"
              entry={entry}
              action={saveRoute}
              badges={routeWarningBadges(entry.record)}
            />
          ))}
        </div>
      </section>

      <section aria-labelledby="models-heading">
        <h2 id="models-heading" className="mb-3 text-base font-bold">
          Models
        </h2>
        <div className="grid gap-3">
          <RecordForm kind="models" action={saveModel} />
          {snapshot.models.map((entry) => (
            <RecordForm
              key={entry.id}
              kind="models"
              entry={entry}
              action={saveModel}
              badges={modelWarningBadges(entry.record)}
            />
          ))}
        </div>
      </section>

      <section aria-labelledby="adapters-heading">
        <h2 id="adapters-heading" className="mb-3 text-base font-bold">
          Adapters
        </h2>
        <div className="grid gap-3">
          <RecordForm kind="adapters" action={saveAdapter} />
          {snapshot.adapters.map((entry) => (
            <RecordForm
              key={entry.id}
              kind="adapters"
              entry={entry}
              action={saveAdapter}
            />
          ))}
        </div>
      </section>

      <section aria-labelledby="secrets-heading">
        <h2 id="secrets-heading" className="mb-3 text-base font-bold">
          Secrets
        </h2>
        <p className="mb-3 text-sm text-muted-foreground">
          Secrets are write-only. Only metadata is shown; a value is never
          returned to the browser.
        </p>
        <SecretForms secrets={secrets} />
      </section>
    </div>
  );
}
