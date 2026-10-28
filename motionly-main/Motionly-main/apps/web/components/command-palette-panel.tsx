"use client";

// The command palette's dialog. Loaded on demand by command-palette.tsx (the
// first time the palette opens), so cmdk stays out of every page's first load.
import { Command } from "cmdk";
import { Search } from "lucide-react";
import { m } from "motion/react";
import { useRouter } from "next/navigation";
import { useCallback, useMemo } from "react";
import { logout } from "@/app/(auth)/actions";
import { PixelIcon } from "@/components/pixel-icons";
import type {
  PaletteCommand,
  PaletteGroup,
} from "@/components/command-palette";
import { groupCommands } from "@/lib/command-registry.mjs";
import { announceSignOut } from "@/lib/session-storage";
import { useFocusTrap } from "@/lib/useFocusTrap";

export function CommandPalettePanel({
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
        announceSignOut();
        void logout();
      } else if (command.run) {
        command.run();
      }
    },
    [onClose, router],
  );

  return (
    <div className="fixed inset-0 z-[100] flex items-start justify-center p-4 pt-[12vh]">
      {/* Pointer-only backdrop: hidden from assistive tech and the Tab order
          (Escape closes the palette). Opacity animation is fine here. */}
      <div
        aria-hidden="true"
        onClick={onClose}
        className="fixed inset-0 bg-foreground/20 backdrop-blur-sm"
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
                        className="rounded-neu-sm bg-muted px-1.5 py-0.5 text-2xs font-semibold text-muted-foreground shadow-neu-inset-sm"
                      >
                        {command.shortcut}
                      </span>
                    ) : null}
                  </Command.Item>
                ))}
              </Command.Group>
            ))}
          </Command.List>
          <div className="flex items-center gap-4 border-hairline border-t px-4 py-2 text-xxs text-muted-foreground">
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
