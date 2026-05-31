# Frontend Feature Boundaries

`apps/web/src/features/` owns product workflow views and app shell code.

Current modules:

- `app/AppShell.tsx`: React Router shell and product workflow views.

Follow-up extraction should keep each workflow in this tree instead of adding
more route code to `App.tsx`. Shared API clients live in `apps/web/src/api/`.
