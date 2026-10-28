import { useEffect, useRef } from "react";

// Open dialogs, most recent last. Only the topmost one handles Tab and Escape,
// so a dialog opened over another (the command palette over the mobile menu,
// a confirmation over a sheet) closes alone instead of taking every dialog
// with it.
const openTraps: HTMLElement[] = [];

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

function focusableIn(container: HTMLElement) {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (element) => !element.hasAttribute("inert") && !element.closest("[inert]"),
  );
}

/** Whether `container` is the dialog that currently owns the keyboard. */
export function isTopFocusTrap(container: HTMLElement | null) {
  return Boolean(container) && openTraps[openTraps.length - 1] === container;
}

/** Whether any focus trap (dialog, sheet, menu) is open. */
export function hasOpenFocusTrap() {
  return openTraps.length > 0;
}

/**
 * Traps keyboard focus in a dialog while `active`: focus moves inside when it
 * opens, Tab and Shift+Tab wrap, Escape calls `onClose` (when given), and
 * focus returns to the element that opened it when it closes.
 */
export function useFocusTrap<T extends HTMLElement = HTMLDivElement>(
  active: boolean,
  onClose?: () => void,
) {
  const containerRef = useRef<T>(null);
  const previouslyFocusedRef = useRef<HTMLElement | null>(null);
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!active) return;

    const container = containerRef.current;
    if (!container) return;
    previouslyFocusedRef.current = document.activeElement as HTMLElement;
    openTraps.push(container);

    const timer = window.setTimeout(() => {
      if (container.contains(document.activeElement)) return;
      const first = focusableIn(container)[0];
      if (first) first.focus();
      else container.focus();
    }, 20);

    const handleKeyDown = (event: KeyboardEvent) => {
      if (!isTopFocusTrap(container)) return;
      if (event.key === "Escape") {
        if (!onCloseRef.current) return;
        event.preventDefault();
        event.stopPropagation();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") return;

      const elements = focusableIn(container);
      if (elements.length === 0) {
        event.preventDefault();
        container.focus();
        return;
      }

      const first = elements[0];
      const last = elements[elements.length - 1];
      const insideTrap = container.contains(document.activeElement);
      if (event.shiftKey && (document.activeElement === first || !insideTrap)) {
        event.preventDefault();
        last.focus();
      } else if (
        !event.shiftKey &&
        (document.activeElement === last || !insideTrap)
      ) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener("keydown", handleKeyDown);
      const index = openTraps.lastIndexOf(container);
      if (index !== -1) openTraps.splice(index, 1);
      const opener = previouslyFocusedRef.current;
      previouslyFocusedRef.current = null;
      if (opener?.isConnected) opener.focus();
    };
  }, [active]);

  return containerRef;
}
