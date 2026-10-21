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

import { Command } from "cmdk";
import { Search } from "lucide-react";
import { m } from "motion/react";
import { useRouter } from "next/navigation";
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
import { logout } from "@/app/(auth)/actions";
import { PixelIcon, type PixelIconName } from "@/components/pixel-icons";
import {
  commandKey,
  groupCommands,
  isPaletteShortcut,
  mergeCommands,
} from "@/lib/command-registry.mjs";
import { useFocusTrap } from "@/lib/useFocusTrap";
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
  // Cmd/Ctrl+K is swallowed here and never reaches it.
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (isPaletteShortcut(event)) {
        event.preventDefault();
        event.stopPropagation();
        toggle();
      }
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
        className="ml-auto rounded-neu-sm bg-muted px-1.5 py-0.5 text-[11px] font-semibold tabular-nums text-muted-foreground shadow-neu-inset-sm"
      >
        {hint}
      </span>
    </button>
  );
}

// -----------------------------------------------------------------------------
// Dialog. Renders nothing when closed (no hidden DOM — browser tests count
// dialogs and links).
// -----------------------------------------------------------------------------

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

function CommandPalettePanel({
  onClose,
  commands,
}: {
  onClose: () => void;
  commands: PaletteCommand[];
}) {
  const router = useRouter();
  const trapRef = useFocusTrap(true, onClose);
  const groups = useMemo(
    () => groupCommands(commands) as Array<[PaletteGroup, PaletteCommand[]]>,
    [commands],
  );

  const runCommand = useCallback(
    (command: PaletteCommand) => {
      // Close first so focus restoration and navigation don't race the dialog.
      onClose();
      if (command.href) {
        router.push(command.href);
      } else if (command.action === "logout") {
        void logout();
      } else if (command.run) {
        command.run();
      }
    },
    [onClose, router],
  );

  return (
    <div className="fixed inset-0 z-[100] flex items-start justify-center p-4 pt-[12vh]">
      {/* Decorative backdrop. Opacity animation is fine here (aria-hidden). */}
      <button
        type="button"
        aria-label="Close command palette"
        onClick={onClose}
        className="fixed inset-0 cursor-default bg-foreground/20 backdrop-blur-sm"
      />
      <m.div
        ref={trapRef}
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        initial={{ scale: 0.97, y: 8 }}
        animate={{ scale: 1, y: 0 }}
        transition={{ duration: 0.18, ease: "easeOut" }}
        className="relative z-10 w-full max-w-xl overflow-hidden rounded-neu-xl bg-surface shadow-neu-raised-lg"
      >
        <Command label="Command palette" className="flex max-h-[70vh] flex-col">
          <div className="flex items-center gap-2 border-hairline border-b px-4 py-3">
            <Search
              aria-hidden="true"
              strokeWidth={1.75}
              className="size-4 shrink-0 text-muted-foreground"
            />
            <Command.Input
              autoFocus
              placeholder="Search commands…"
              className="h-9 w-full rounded-neu bg-muted px-3 text-sm text-foreground shadow-neu-inset-sm outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-focus"
            />
          </div>
          <Command.List className="max-h-[52vh] overflow-y-auto overflow-x-hidden p-2">
            <Command.Empty className="px-3 py-8 text-center text-sm text-muted-foreground">
              No matching commands
            </Command.Empty>
            {groups.map(([group, groupCommandsList]) => (
              <Command.Group
                key={group}
                heading={group}
                className="px-1 py-1 text-xs text-muted-foreground [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-xs [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:text-muted-foreground"
              >
                {groupCommandsList.map((command: PaletteCommand) => (
                  <Command.Item
                    key={command.id}
                    value={`${command.label} ${(command.keywords ?? []).join(" ")}`}
                    onSelect={() => runCommand(command)}
                    className="group flex min-h-11 cursor-pointer items-center gap-3 rounded-neu px-2 py-2 text-sm text-foreground transition-shadow data-[selected=true]:bg-surface data-[selected=true]:text-primary data-[selected=true]:shadow-neu-glow"
                  >
                    <span className="flex size-8 shrink-0 items-center justify-center rounded-neu-sm bg-muted text-muted-foreground shadow-neu-inset-sm group-data-[selected=true]:text-primary">
                      {command.icon ? (
                        <PixelIcon
                          name={command.icon}
                          aria-hidden
                          className="size-4"
                        />
                      ) : (
                        <PixelIcon
                          name="project"
                          aria-hidden
                          className="size-4"
                        />
                      )}
                    </span>
                    <span className="flex-1 truncate">{command.label}</span>
                    {command.shortcut ? (
                      <span
                        aria-hidden
                        className="rounded-neu-sm bg-muted px-1.5 py-0.5 text-[10px] font-semibold text-muted-foreground shadow-neu-inset-sm"
                      >
                        {command.shortcut}
                      </span>
                    ) : null}
                  </Command.Item>
                ))}
              </Command.Group>
            ))}
          </Command.List>
          <div className="flex items-center gap-4 border-hairline border-t px-4 py-2 text-[11px] text-muted-foreground">
            <span className="flex items-center gap-1">
              <kbd className="rounded-neu-sm bg-muted px-1.5 py-0.5 shadow-neu-inset-sm">
                ↑↓
              </kbd>
              navigate
            </span>
            <span className="flex items-center gap-1">
              <kbd className="rounded-neu-sm bg-muted px-1.5 py-0.5 shadow-neu-inset-sm">
                ↵
              </kbd>
              open
            </span>
            <span className="flex items-center gap-1">
              <kbd className="rounded-neu-sm bg-muted px-1.5 py-0.5 shadow-neu-inset-sm">
                Esc
              </kbd>
              close
            </span>
          </div>
        </Command>
      </m.div>
    </div>
  );
}
