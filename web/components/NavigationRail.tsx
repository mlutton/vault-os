const destinations = [
  { label: "Cockpit", href: "/", status: "available" },
  { label: "Finance", href: "/finance", status: "planned", release: "v1.1" },
  { label: "Writing", href: "/writing", status: "planned", release: "v1.2" },
  { label: "Research", href: "/research", status: "planned", release: "v1.2" },
] as const;

export function NavigationRail() {
  return (
    <nav className="route-menu" aria-label="Primary navigation">
      <p className="route-menu__eyebrow">Workspace</p>
      <p className="visually-hidden">Three destinations are planned and not yet built.</p>
      <ul className="route-menu__list">
        {destinations.map((destination) => (
          <li key={destination.href}>
            {destination.status === "available" ? (
              // Cockpit is hardcoded active while it is the only route; the
              // "exposes exactly one current page" test guards this assumption.
              <a className="route-menu__current" href={destination.href} aria-current="page">
                <span>{destination.label}</span>
                <span className="route-menu__marker" aria-hidden="true" />
              </a>
            ) : (
              <div className="route-menu__deferred">
                <span>{destination.label}</span>
                <span className="route-menu__release">Planned {destination.release}</span>
              </div>
            )}
          </li>
        ))}
      </ul>
    </nav>
  );
}
