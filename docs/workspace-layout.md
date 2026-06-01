# Workspace Layout

This app is the integration layer for the existing koubei repos.

## App Root

`/Users/xyc/Documents/codexwork/carfeedback`

## Runtime Areas

- `apps/web`: Next.js frontend
- `apps/api`: FastAPI service
- `apps/worker`: queue worker and deterministic wrappers
- `config`: dependency and environment configuration
- `ops`: nginx and operational scripts
- `storage/jobs`: local artifact root for job outputs during MVP

## External Dependencies

The MVP keeps the current repos in place and resolves them relative to `WORKSPACE_ROOT`:

- `data/repos/vehicle-id-finder`
- `data/repos/auto-koubei-collector`
- `data/repos/dcd-koubei-collector`
- `data/repos/koubei-postprocess`
- `data/repos/koubei-keyword-summary-skill`
- `koubei-wordcloud`

## Artifact Convention

Each job writes to a dedicated root:

```text
/srv/koubei/jobs/<job_id>/
  meta/
  progress/
  logs/
  inputs/
  outputs/
    raw/
    postprocess/
    summary/
    wordcloud/
    ai/
```

The API and Web layers must only read job data through stable wrappers and assembled DTOs, not by directly traversing arbitrary repo internals.
