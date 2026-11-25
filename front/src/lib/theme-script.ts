/**
 * The theme's storage key and the inline script of <head>, in a module of
 * their own on purpose: this file must NOT be a client module ("use client").
 *
 * The root layout is a Server Component. A value it imports from a client
 * module reaches the browser as a reference that is only resolved once that
 * module's chunk has loaded. With the script's text coming from one, React
 * suspended inside <head> whenever the layout chunk arrived after hydration had
 * started (a slow or busy machine), lost its place in the document and failed
 * the hydration of the whole page (React error #418 on <html>). As a plain
 * string it travels inside the page itself.
 */
export const THEME_STORAGE_KEY = 'oc.theme';

/**
 * Inline script for <head>: applies the stored theme before the first paint.
 * Dark is the default; "system" follows the OS (map 1.2).
 */
export const THEME_INIT_SCRIPT = `(function(){try{var p=localStorage.getItem('${THEME_STORAGE_KEY}')||'dark';var t=p==='system'?(matchMedia('(prefers-color-scheme: light)').matches?'light':'dark'):p;document.documentElement.setAttribute('data-theme',t==='light'?'light':'dark');}catch(e){document.documentElement.setAttribute('data-theme','dark');}})();`;

