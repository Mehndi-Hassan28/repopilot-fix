"use client";

import { useEffect, useRef } from "react";

interface UseFocusTrapOptions {
  isOpen: boolean;
  onClose?: () => void;
  initialFocusRef?: React.RefObject<HTMLElement | null>;
  closeOnEscape?: boolean;
}

/**
 * Reusable accessibility hook for modals and dialogs:
 * 1. Saves document.activeElement before opening and restores focus on close.
 * 2. Focuses the initialFocusRef or first focusable element when opened.
 * 3. Traps Tab and Shift+Tab key navigation strictly within the dialog container.
 * 4. Handles Escape key to trigger onClose.
 */
export function useFocusTrap({
  isOpen,
  onClose,
  initialFocusRef,
  closeOnEscape = true,
}: UseFocusTrapOptions) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const previousActiveElementRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!isOpen) return;

    // Capture currently focused element prior to modal opening
    previousActiveElementRef.current = document.activeElement as HTMLElement | null;

    const container = containerRef.current;
    const focusableSelector =
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

    // Focus initial element or first focusable element
    const focusTimer = setTimeout(() => {
      if (initialFocusRef?.current) {
        initialFocusRef.current.focus();
      } else if (container) {
        const focusableElements = container.querySelectorAll<HTMLElement>(focusableSelector);
        if (focusableElements.length > 0) {
          focusableElements[0].focus();
        }
      }
    }, 20);

    const handleKeyDown = (e: KeyboardEvent) => {
      if (closeOnEscape && e.key === "Escape") {
        e.preventDefault();
        onClose?.();
        return;
      }

      if (e.key === "Tab" && container) {
        const focusableElements = container.querySelectorAll<HTMLElement>(focusableSelector);
        if (focusableElements.length === 0) return;

        const first = focusableElements[0];
        const last = focusableElements[focusableElements.length - 1];

        if (e.shiftKey) {
          if (document.activeElement === first) {
            e.preventDefault();
            last.focus();
          }
        } else {
          if (document.activeElement === last) {
            e.preventDefault();
            first.focus();
          }
        }
      }
    };

    window.addEventListener("keydown", handleKeyDown);

    return () => {
      clearTimeout(focusTimer);
      window.removeEventListener("keydown", handleKeyDown);
      // Restore focus on close
      const toRestore = previousActiveElementRef.current;
      if (toRestore && typeof toRestore.focus === "function") {
        setTimeout(() => toRestore.focus(), 0);
      }
    };
  }, [isOpen, onClose, initialFocusRef, closeOnEscape]);

  return containerRef;
}
