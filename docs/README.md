# Documentation

This folder holds the Thumbly user guide as MDX pages for [Fumadocs](https://fumadocs.dev).
Engineering notes for contributors live in [`apps/device/AGENTS.md`](../apps/device/AGENTS.md).

- One `.mdx` file per page, with `title` and `description` in the frontmatter. Other files,
  such as this README, are not published.
- `meta.json` in each folder sets the sidebar title and page order.
- Link between pages with `/docs/<folder>/<page>`.
- Available components: `<Callout>` (`type="warn"` for warnings, `type="success"` for a
  checkpoint), `<Steps>` / `<Step>`, `<Cards>` / `<Card>`, `<Tabs>` / `<Tab>`. Images zoom on click.
- The journey is three numbered steps, one root folder each, with the number in the folder
  title: `1. Install Thumbly`, `2. Connect your iPhone` and `3. Connect your AI`. The sidebar
  shows the number as a badge. Optional remote access lives under `Optional`; troubleshooting
  and reference pages live under `Help`.
- Name each folder and file after its title in lowercase kebab-case, without the step number:
  `2. Connect your iPhone` is `connect-your-iphone/`, "Plug in and tap Trust" is
  `plug-in-and-trust.mdx`. A folder's own page is `index.mdx`. Rename the file when its title
  changes, and update the links to it.
- Use everyday language in navigation. Explain terms such as pairing and MCP where needed.
- End journey pages with a clear next step. Match setup order to the Thumbly app: cable, Trust,
  Developer Mode, then connect an AI.
- Use `{/* TODO: ... */}` for unfinished parts; it does not render.
- Describe only behaviour that has shipped.

Contributions follow [CONTRIBUTING.md](../CONTRIBUTING.md).
