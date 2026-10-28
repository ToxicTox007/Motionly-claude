"use client";

// =============================================================================
// Command palette (⌘K) — public API consumed by the next wave.
//
//   export type PaletteGroup = "Navigate" | "Create" | "Editor" | "Account" | "Help";
//   export type PaletteCommand = {
//     id: string;            // unique; later registrations with the same id replace earlier ones
//     label: string;         // shown text
//     group: PaletteGroup;
//     href?: string;         // navigate with next/navigation router.push
//     action?: "logout";     // named server-safe action (server components may pass this)
//     run?: () => void;      // client-only callback (only from client components)
//     keywords?: string[];
//     icon?: PixelIconName;
//     shortcut?: string;     // display hint, e.g. "Alt+Shift+M"
//   };
//
//   <CommandPaletteProvider>{children}</CommandPaletteProvider>
//       Mounts the registry context, the global Cmd/Ctrl+K listener and the dialog.
//       Mounted once by app/layout.tsx.
//
//   <RegisterCommands source="..." commands={[...]} />
//       Client component usable from SERVER components. Commands must be
//       serializable (href / action only, no `run`).
//
//   useRegisterCommands(source, commands)
//       For client components. Unregisters on unmount. Safe to pass a fresh
//       array literal every render — it compares by a stable content key.
//
//   useCommandPalette() -> { open, close, isOpen }
//
//   <CommandPaletteTrigger className? compact? />
//       A button that opens the palette. `compact` renders icon-only. Accessible
//       name is "Search commands" in both modes.
// =============================================================================

import { Search } from "lucide-react";
import dynamic from "next/dynamic";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import type { PixelIconName } from "@/components/pixel-icons";
import {
  commandKey,
  isPaletteShortcut,
  mergeCommands,
} from "@/lib/command-registry.mjs";
import { shouldIgnoreShortcut } from "@/lib/keyboard-shortcuts";
import { cn } from "@/lib/utils";

export type PaletteGroup =
  | "Navigate"
  | "Create"
  | "Editor"
  | "Account"
  | "Help";

export type PaletteCommand = {
  id: string;
  label: string;
  group: PaletteGroup;
  href?: string;
  action?: "logout";
  run?: () => void;
  keywords?: string[];
  icon?: PixelIconName;
  shortcut?: string;
};

type RegistryContextValue = {
  register: (source: string, commands: PaletteCommand[]) => void;
  unregister: (source: string) => void;
  commands: PaletteCommand[];
};

type PaletteContextValue = {
  open: () => void;
  close: () => void;
  isOpen: boolean;
};

const RegistryContext = createContext<RegistryContextValue | null>(null);
const PaletteContext = createContext<PaletteContextValue | null>(null);

export function CommandPaletteProvider({ children }: { children: ReactNode }) {
  const [sources, setSources] = useState<Map<string, PaletteCommand[]>>(
    () => new Map(),
  );
  const [isOpen, setIsOpen] = useState(false);

  const open = useCallback(() => setIsOpen(true), []);
  const close = useCallback(() => setIsOpen(false), []);
  const toggle = useCallback(() => setIsOpen((value) => !value), []);

  const register = useCallback((source: string, commands: PaletteCommand[]) => {
    setSources((previous) => {
      const next = new Map(previous);
      // Re-insert so this source becomes the most recent (latest wins).
      next.delete(source);
      next.set(source, commands);
      return next;
    });
  }, []);

  const unregister = useCallback((source: string) => {
    setSources((previous) => {
      if (!previous.has(source)) return previous;
      const next = new Map(previous);
      next.delete(source);
      return next;
    });
  }, []);

  const commands = useMemo(
    () => mergeCommands(sources) as PaletteCommand[],
    [sources],
  );

  // Single global shortcut handler, in the capture phase so it runs before the
  // editor timeline's window keydown handler (which treats plain "k" as pause).
  // Cmd/Ctrl+K is swallowed here and never reaches it. It still works from a
  // search box or over another dialog, but not inside a textarea or rich-text
  // region, where Cmd/Ctrl+K has its own meaning (insert link).
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (!isPaletteShortcut(event)) return;
      if (
        shouldIgnoreShortcut(event, {
          editable: "rich-text",
          allowInDialog: true,
        })
      ) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      toggle();
    };
    window.addEventListener("keydown", handler, { capture: true });
    return () =>
      window.removeEventListener("keydown", handler, { capture: true });
  }, [toggle]);

  const registryValue = useMemo<RegistryContextValue>(
    () => ({ register, unregister, commands }),
    [register, unregister, commands],
  );
  const paletteValue = useMemo<PaletteContextValue>(
    () => ({ open, close, isOpen }),
    [open, close, isOpen],
  );

  return (
    <RegistryContext.Provider value={registryValue}>
      <PaletteContext.Provider value={paletteValue}>
        {children}
        <CommandPaletteDialog
          open={isOpen}
          onClose={close}
          commands={commands}
        />
      </PaletteContext.Provider>
    </RegistryContext.Provider>
  );
}

function useRegistry(): RegistryContextValue {
  const context = useContext(RegistryContext);
  if (!context) {
    throw new Error("Command palette registry is not mounted");
  }
  return context;
}

export function useCommandPalette(): PaletteContextValue {
  const context = useContext(PaletteContext);
  if (!context) {
    throw new Error(
      "useCommandPalette must be used within CommandPaletteProvider",
    );
  }
  return context;
}

// Register commands from a client component. Unregisters on unmount. The effect
// depends on a stable content key, so a fresh array literal with the same
// contents on every render will not cause a re-register loop.
export function useRegisterCommands(
  source: string,
  commands: PaletteCommand[],
): void {
  const registry = useContext(RegistryContext);
  const commandsRef = useRef(commands);
  const key = commandKey(commands);

  // Keep the latest commands in a ref without touching it during render.
  useEffect(() => {
    commandsRef.current = commands;
  });

  useEffect(() => {
    if (!registry) return;
    registry.register(source, commandsRef.current);
    return () => registry.unregister(source);
  }, [registry, source, key]);
}

// Register serializable commands (href / action only) from a SERVER component
// by rendering this client component. Renders no DOM.
export function RegisterCommands({
  source,
  commands,
}: {
  source: string;
  commands: PaletteCommand[];
}): null {
  useRegisterCommands(source, commands);
  return null;
}

// -----------------------------------------------------------------------------
// Trigger button.
// -----------------------------------------------------------------------------

// A no-op external store: the platform never changes during a session, so there
// is nothing to subscribe to. The server snapshot is neutral ("⌘K"); the client
// snapshot reads navigator once. React reconciles the two without a hydration
// warning because useSyncExternalStore is designed for exactly this.
function subscribeNoop() {
  return () => {};
}

function getServerKeyHint() {
  return "⌘K";
}

function getClientKeyHint() {
  const platform =
    typeof navigator !== "undefined"
      ? navigator.platform || navigator.userAgent
      : "";
  return /Mac|iPhone|iPad|iPod/i.test(platform) ? "⌘K" : "Ctrl K";
}

export function CommandPaletteTrigger({
  className,
  compact = false,
}: {
  className?: string;
  compact?: boolean;
}) {
  const { open } = useCommandPalette();
  // Decide the key hint only on the client (after mount) so the server and the
  // first client render agree. useSyncExternalStore gives a stable server
  // snapshot (neutral "⌘K") and a client snapshot without a setState-in-effect.
  const hint = useSyncExternalStore(
    subscribeNoop,
    getClientKeyHint,
    getServerKeyHint,
  );

  if (compact) {
    return (
      <button
        type="button"
        onClick={open}
        aria-keyshortcuts="Control+K Meta+K"
        aria-label="Search commands"
        className={cn(
          "neu-pressable inline-flex size-11 items-center justify-center rounded-neu text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2 focus-visible:ring-offset-background",
          className,
        )}
      >
        <Search aria-hidden="true" strokeWidth={1.75} className="size-5" />
      </button>
    );
  }

  return (
    <button
      type="button"
      onClick={open}
      aria-keyshortcuts="Control+K Meta+K"
      aria-label="Search commands"
      className={cn(
        "neu-pressable inline-flex min-h-11 items-center gap-2 rounded-neu px-3 py-2 text-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2 focus-visible:ring-offset-background",
        className,
      )}
    >
      <Search
        aria-hidden="true"
        strokeWidth={1.75}
        className="size-4 shrink-0"
      />
      <span>Search</span>
      <span
        aria-hidden
        className="ml-auto rounded-neu-sm bg-muted px-1.5 py-0.5 text-xxs font-semibold tabular-nums text-muted-foreground shadow-neu-inset-sm"
      >
        {hint}
      </span>
    </button>
  );
}

// -----------------------------------------------------------------------------
// Dialog. Renders nothing when closed (no hidden DOM — browser tests count
// dialogs and links). The panel and cmdk load on first open, so pages that
// never open the palette do not download them (FE-40).
// -----------------------------------------------------------------------------

const CommandPalettePanel = dynamic(
  () =>
    import("./command-palette-panel").then((mod) => mod.CommandPalettePanel),
  { ssr: false },
);

function CommandPaletteDialog({
  open,
  onClose,
  commands,
}: {
  open: boolean;
  onClose: () => void;
  commands: PaletteCommand[];
}) {
  if (!open) return null;
  return <CommandPalettePanel onClose={onClose} commands={commands} />;
}
