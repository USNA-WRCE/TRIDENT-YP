export function isSarBehavior(behavior?: string | null): boolean {
  return behavior === "search_grid" || behavior === "mob_search";
}
