'use client';

import { useEffect, useRef, type RefObject } from 'react';

const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

export function focusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (element) => !element.hasAttribute('inert') && element.getAttribute('aria-hidden') !== 'true',
  );
}

/** Open modal layers, bottom to top. Escape and the focus trap only act on the top one. */
const stack: symbol[] = [];
let lockCount = 0;
let previousOverflow = '';

function lockScroll() {
  if (lockCount === 0) {
    previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
  }
  lockCount += 1;
}

function unlockScroll() {
  lockCount = Math.max(0, lockCount - 1);
  if (lockCount === 0) document.body.style.overflow = previousOverflow;
}

export interface ModalLayerOptions {
  open: boolean;
  onClose: () => void;
  /** Escape and backdrop close the layer. False for flows that must be answered (session expired). */
  dismissable?: boolean;
}

/**
 * Behaviour shared by Dialog and Drawer: moves focus inside on open, keeps Tab
 * inside (focus trap), closes with Escape, locks page scroll and gives focus
 * back to the element that opened it.
 */
export function useModalLayer<T extends HTMLElement>(
  { open, onClose, dismissable = true }: ModalLayerOptions,
): RefObject<T | null> {
  const ref = useRef<T | null>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const dismissableRef = useRef(dismissable);
  dismissableRef.current = dismissable;

  useEffect(() => {
    if (!open) return;
    const node = ref.current;
    if (!node) return;
    const token = Symbol('layer');
    stack.push(token);
    lockScroll();
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;

    const initial =
      node.querySelector<HTMLElement>('[data-autofocus]') ?? focusableElements(node)[0] ?? node;
    initial.focus({ preventScroll: true });

    const onKeyDown = (event: KeyboardEvent) => {
      if (stack[stack.length - 1] !== token) return;
      if (event.key === 'Escape') {
        if (dismissableRef.current) {
          event.stopPropagation();
          onCloseRef.current();
        }
        return;
      }
      if (event.key !== 'Tab') return;
      const items = focusableElements(node);
      if (items.length === 0) {
        event.preventDefault();
        node.focus();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && (active === first || !node.contains(active))) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && (active === last || !node.contains(active))) {
        event.preventDefault();
        first?.focus();
      }
    };
    const onFocusIn = (event: FocusEvent) => {
      if (stack[stack.length - 1] !== token) return;
      if (event.target instanceof Node && !node.contains(event.target)) {
        (focusableElements(node)[0] ?? node).focus({ preventScroll: true });
      }
    };
    document.addEventListener('keydown', onKeyDown, true);
    document.addEventListener('focusin', onFocusIn);
    return () => {
      document.removeEventListener('keydown', onKeyDown, true);
      document.removeEventListener('focusin', onFocusIn);
      const index = stack.indexOf(token);
      if (index >= 0) stack.splice(index, 1);
      unlockScroll();
      if (opener && document.contains(opener)) opener.focus({ preventScroll: true });
    };
  }, [open]);

  return ref;
}

/** Closes a popover or menu on outside pointer-down and on Escape. */
export function useDismiss(
  open: boolean,
  refs: Array<RefObject<HTMLElement | null>>,
  onDismiss: (reason: 'outside' | 'escape') => void,
): void {
  const onDismissRef = useRef(onDismiss);
  onDismissRef.current = onDismiss;
  const refsRef = useRef(refs);
  refsRef.current = refs;
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent | MouseEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (refsRef.current.some((ref) => ref.current?.contains(target))) return;
      onDismissRef.current('outside');
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        onDismissRef.current('escape');
      }
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);
}
