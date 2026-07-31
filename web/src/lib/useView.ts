import { useCallback, useSyncExternalStore } from "react";

export type AppView = "tasks" | "stats";

/**
 * The console's top-level view lives in the URL, not in component state.
 *
 * `?view=stats` deep-links straight to the usage page — the desktop app's
 * quota surfaces (notch panel) use it so "see more detail" is one step. That
 * link only works as a *starting* point if the URL keeps tracking the view
 * afterwards: otherwise a reload replays how the user arrived instead of
 * showing what they were last looking at, and the desktop window's reload
 * button yanks them back to the deep-linked page.
 *
 * So the URL is the single source of truth and `view` is derived from it —
 * no mirrored `useState` that can drift out of sync with the address bar.
 */

const NAV_EVENT = "fluxion:navigate";

/** URL query string → view. Pure; the round-trip partner of `searchWithView`. */
export function viewFromSearch(search: string): AppView {
  return new URLSearchParams(search).get("view") === "stats" ? "stats" : "tasks";
}

/**
 * View → URL query string, preserving every other param (`lang`, `token`).
 * "tasks" is the default, so it drops the param rather than spelling it out.
 */
export function searchWithView(search: string, view: AppView): string {
  const params = new URLSearchParams(search);
  if (view === "stats") params.set("view", "stats");
  else params.delete("view");
  const query = params.toString();
  return query ? `?${query}` : "";
}

// history.pushState fires no event of its own, so navigations we initiate are
// announced on the window alongside the back/forward ones we listen for.
function subscribe(onChange: () => void): () => void {
  window.addEventListener("popstate", onChange);
  window.addEventListener(NAV_EVENT, onChange);
  return () => {
    window.removeEventListener("popstate", onChange);
    window.removeEventListener(NAV_EVENT, onChange);
  };
}

export function useView(): [AppView, (next: AppView) => void] {
  const view = useSyncExternalStore(
    subscribe,
    () => viewFromSearch(window.location.search),
    () => "tasks" as AppView,
  );

  // pushState (not replace): the console is served to real browsers too
  // (README documents http://127.0.0.1:8765), where back/forward across the
  // two views is the expected behaviour. Re-selecting the current view is a
  // no-op so repeated clicks can't stack duplicate history entries.
  const setView = useCallback((next: AppView) => {
    if (viewFromSearch(window.location.search) === next) return;
    const { pathname, search, hash } = window.location;
    window.history.pushState(null, "", `${pathname}${searchWithView(search, next)}${hash}`);
    window.dispatchEvent(new Event(NAV_EVENT));
  }, []);

  return [view, setView];
}
