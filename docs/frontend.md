# Map and table interaction

The map keeps the global resolution-3 Arrow layer available while resolution-7
cells load. Dynamic fine tiles commit a complete visible viewport before its
complete guard ring. GPU buffers warm one tile per frame, and the visible
handoff waits for coverage of the actual camera footprint, including rotated and pitched
views. An older complete camera generation can remain visible while panning
within the same habitat and boundary source. Changing that source immediately
uses correctly filtered coarse context until new fine coverage is ready.

Decoded generation references are retained independently of the bounded tile
cache. Valid empty tiles count toward coverage. A failed tile receives one
bounded retry; a failed current signature can be scheduled again after a later
settled camera event. Source replacement and route disposal invalidate old work.
These are rendering contracts, not measured global performance claims.

The colour menu offers linear, logarithmic and exact Jenks natural breaks.
Logarithmic colour is the default; offset and gamma tune its contrast. Jenks
classifies the filtered global coarse snapshot in a worker, giving all displayed
layers a common reference rather than changing its breaks with each fine viewport.
Leaving the route cancels initialization before it can create a classification
worker; mounted workers terminate during route cleanup.
Per-species normalization divides priority by richness, not area. Palette and
transparency controls affect display only; they do not change the score.

Species search binds suggestions to the current text and blocks selection while
loading. Options support pointer activation, combobox arrows/Enter and native
button Enter/Space. Selecting or closing search invalidates pending queries.
H3 search accepts resolutions 3 and 7, navigates to the cell and retains its native
resolution in details. An index can be valid without belonging to the loaded
coverage or selected jurisdiction.

Table parameter changes supersede pending requests from the first load onward,
including after errors. Cell details abort owned work when closed or removed.
Open cell rows update their ordering, priorities and DNA-gap labels immediately
when sorting or shared weights change, including a weight reset.
Window shortcuts leave native controls, links, editable content and previously
handled events alone. Place filters, weights and selections remain shared across
routes; Reset all clears table filters and spatial scope.

The map/sidebar and colour/search controls adapt to narrow viewports and respect
reduced motion. `/methodology` explains evidence and uncertainty; the existing
`/about-data` route remains available for older links. The code release does not
include real serving data, and this UI does not grant redistribution rights.
See [data policy](../DATA_POLICY.md) and [methodology](../methodology.md).
