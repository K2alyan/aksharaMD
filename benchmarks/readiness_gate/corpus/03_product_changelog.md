# Product Changelog — Orbit Analytics

## v3.2.0 — January 15, 2025

Orbit Analytics version 3.2.0 was released on January 15, 2025.

### Added
- Real-time collaboration: multiple users can now edit dashboards simultaneously.
- PDF export for all dashboard types with selectable page sizes (A4, Letter, Tabloid).
- Custom color palette editor with hex input and accessibility contrast checker.

### Fixed
- Dashboard load time reduced by 40% through query plan caching.
- Tooltip positioning corrected for charts rendered in RTL layouts.
- CSV export no longer truncates rows beyond 65,536 lines.

### Deprecated
- The v2 widget API is deprecated as of this release. It will be removed in v4.0.
  Migrate to the v3 widget API before upgrading to v4.

---

## v3.1.0 — November 3, 2024

### Added
- Dark mode for all dashboard and report views.
- Scheduled report delivery via email with configurable cadence.

### Fixed
- Fixed memory leak in the streaming data connector under sustained load.
- Resolved time zone offset errors in weekly aggregation queries.

---

## v2.8.0 — August 22, 2024

### Breaking changes
- The legacy `/api/v1/reports` endpoint was deprecated in this release.
  All callers must migrate to `/api/v3/reports` by the v3.0 end-of-life date.

### Added
- Multi-tenant workspace isolation with per-workspace encryption keys.

---

## v2.0.0 — March 10, 2024

### Breaking changes
- Minimum required Python version raised from 3.9 to 3.11.
- The `OldDashboardClient` class was removed after 6 months of deprecation.
