# Thumbly landing page

Live at https://timurcatakli.github.io/thumbly/

`index.html` is the page exported from the design as a single bundle. On every push to `main`
that touches `site/` or `VERSION`, `.github/workflows/pages.yml` runs `build.py`, which:

- unpacks the bundle into plain HTML and files under `assets/`, so crawlers and link previews
  can read the page;
- adds the title, description, canonical URL, Open Graph and X card tags, JSON-LD and icons;
- applies the copy changes in `COPY` and the single-colour logos in `icons/`;
- adds `sitemap.xml` and the files in `public/` (share image, favicons, web manifest).

## Updating

Re-export `index.html` from the design and push it. The page deploys in about a minute.

Preview locally:

```sh
python3 site/build.py _site
mkdir -p /tmp/preview && ln -sfn "$PWD/_site" /tmp/preview/thumbly
python3 -m http.server -d /tmp/preview 8000   # open http://127.0.0.1:8000/thumbly/
```

`public/og.png` (1200 × 630) is the image WhatsApp, X, Slack and LinkedIn show when the link is
shared. After a change, refresh the cached preview with the
[Facebook sharing debugger](https://developers.facebook.com/tools/debug/) (WhatsApp uses the same
crawler).
