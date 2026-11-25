/** Sends the browser to an external page (the payment provider's checkout). Apart so tests can replace it. */
export function leaveTo(url: string): void {
  window.location.assign(url);
}
