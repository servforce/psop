# PSOP Static Admin Scaffold

This directory now keeps only the frontend scaffold that is independent from the old business UI.

## What remains

- `TailwindCSS v4` local build pipeline
- `Alpine.js` shell for simple interactive pages
- Static preview server and Jest setup
- Generic admin homepage that explains the preserved scaffold

## What was removed

- Legacy Skills list and detail pages
- Legacy page fragments and page-specific Alpine components
- Old mock data tied to the previous business flows

## Local development

```bash
cd static
npm ci
npm run build:css
npm run dev
```

Or from the repo root:

```bash
scripts/dev/build-web.sh
scripts/dev/test-web.sh
scripts/dev/run-web.sh
```
