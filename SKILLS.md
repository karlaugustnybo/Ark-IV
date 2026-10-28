# ARK-IV skill routing

Use only the workflow relevant to the task. These are global skills, not dependencies.

| Task | Skills |
| --- | --- |
| Pipeline, scoring, API, or changes across layers | `ark-iv-workflow` |
| Svelte components and state | `svelte-core-bestpractices` |
| Map rendering | `deckgl`, then `deckgl-core`, `deckgl-layers`, or `deckgl-basemap` as needed |
| Custom map layers/shaders | `deckgl-custom-layer` |
| Python tooling | `uv-package-manager` |
| Reproducible experiments | `marimo-notebook` or `marimo-pair` |
| Research papers | Relevant literature tool only when its subject/source fits |
| Requested PR creation/monitoring | `file-pr` / `babysit-pr` |

ARK is Svelte, so `deckgl-react` is normally unnecessary. Business KPI workflows do
not define scientific scoring. Follow methodology and publication policy first.

Global guide: `~/Code/agent-config/SKILLS.md`. To share a project-specific skill with
collaborators, put its folder in `.agents/skills/` and commit it with the repository.
