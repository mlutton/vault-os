import type { ReactNode } from "react";

import { NavigationRail } from "./NavigationRail";

export function CockpitFrame({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <div className="workspace-frame">
      <a className="skip-link" href="#workspace-content">
        Skip to content
      </a>
      <div className="workspace-rail">
        <div className="workspace-brand">
          <span className="workspace-brand__symbol" aria-hidden="true">
            V
          </span>
          <span className="workspace-brand__name">VaultOS</span>
        </div>
        <NavigationRail />
        <p className="workspace-rail__note">Local-first operations</p>
      </div>
      <main className="workspace-content" id="workspace-content" tabIndex={-1}>
        {children}
      </main>
    </div>
  );
}
