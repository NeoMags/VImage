// "Sorted by AI" in the sidebar (T3): the most common AI tags as virtual groups. A group is the tag
// filter on All photos; nothing is moved or copied, and groups change as photos get tagged.
import { useAiGroups } from "@imagevault/api-client";
import { useState } from "react";
import { Link, useLocation } from "react-router";

import { num } from "../format";
import { setFilters, useFilters } from "../gallery/filters";

const FIRST = 10;

export function AiGroupsNav() {
  const groups = useAiGroups();
  const filters = useFilters();
  const { pathname } = useLocation();
  const [all, setAll] = useState(false);
  const rows = groups.data ?? [];
  if (!rows.length) return null;
  const only = Object.keys(filters).length === 1 && Array.isArray(filters.tag) && filters.tag.length === 1 ? filters.tag[0] : null;
  const shown = all ? rows : rows.slice(0, FIRST);
  return (
    <div className="nav-collections" role="region" aria-label="Sorted by AI">
      <div className="nav-head">
        <span>Sorted by AI</span>
      </div>
      <ul className="tree">
        {shown.map((g) => {
          const active = pathname === "/photos" && only === g.id;
          return (
            <li key={g.id}>
              <div className="tree-row">
                <span className="twisty" />
                <Link
                  to="/photos"
                  className={active ? "active" : undefined}
                  aria-current={active ? "page" : undefined}
                  onClick={() => setFilters({ tag: [g.id] })}
                >
                  <span className="name">{g.name}</span>
                  <span className="muted small">{num(g.count)}</span>
                </Link>
              </div>
            </li>
          );
        })}
      </ul>
      {rows.length > FIRST ? (
        <button type="button" className="link small nav-more" onClick={() => setAll((v) => !v)}>
          {all ? "Show fewer" : `Show all ${rows.length}`}
        </button>
      ) : null}
    </div>
  );
}
