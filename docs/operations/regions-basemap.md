# Regions Map Basemap

The Regions page draws its map from a landmass outline shipped inside the dashboard image
(`frontend/public/geo/`, derived from public-domain Natural Earth data). Out of the box it makes
no outbound request, needs no API key, and works in air-gapped installs. Nothing about where
your clusters are leaves the browser.

This page applies to 1.0.0-RC4 and later. Older compose files do not pass these variables to
the frontend.

---

## Use a tile provider

For street-level detail, point `MAP_TILE_URL` at an XYZ tile template from a provider you are
allowed to use:

```bash
MAP_TILE_URL=https://tiles.example.com/{z}/{x}/{y}.png
MAP_TILE_ATTRIBUTION=Your Provider
MAP_TILE_ATTRIBUTION_URL=https://example.com/
MAP_TILE_MAX_ZOOM=18
```

Check the provider's terms first: most require the attribution, and many need an API key in
the URL or refuse anonymous traffic.

| Variable | When unset | Meaning |
|---|---|---|
| `MAP_TILE_URL` | the bundled outline map | An `http` or `https` URL containing `{z}`, `{x}` and `{y}` |
| `MAP_TILE_URL_DARK` | `MAP_TILE_URL` in both themes | A second template used while the console is in dark mode |
| `MAP_TILE_ATTRIBUTION` | no credit shown | Plain text credit shown on the map. It is escaped, so HTML is shown as text |
| `MAP_TILE_ATTRIBUTION_URL` | the credit is not a link | `http` or `https` link for the credit; any other scheme is ignored |
| `MAP_TILE_SUBDOMAINS` | `abc` when the template contains `{s}` | The letters substituted for `{s}` |
| `MAP_TILE_MAX_ZOOM` | `18` | Deepest zoom level, clamped to 1 to 22 |

## Where to set them

The variables belong to the frontend service. In production the frontend has no `env_file`,
so they reach it through the `environment` block of `docker-compose.prod.yml`, which lists all
six from 1.0.0-RC4 onward. Put the values in the `.env` next to that compose file
([dashboard deployment guide, Step 6](../deployment/vm4-dashboard.md#step-6-create-environment-file)).

They are read at runtime, so a change needs the frontend container recreated but no rebuild:

```bash
docker compose -f docker-compose.prod.yml up -d frontend
```

## Validation and fallback

- A `MAP_TILE_URL` that is not an `http` or `https` URL with `{z}`, `{x}` and `{y}` is ignored.
  The frontend log shows `[basemap] MAP_TILE_URL is not a usable XYZ tile template` when the
  Regions page renders, and the bundled outline map is drawn instead.
- An unusable `MAP_TILE_URL_DARK` is ignored with its own log line, and `MAP_TILE_URL` is used
  in both themes.
- A provider that stops answering does not leave a grid of broken images. After four tiles
  fail to load, the map switches to the bundled outline and says "The configured map tiles
  could not be loaded, so the built-in outline map is shown instead."

## Privacy

With a provider set, each viewer's browser requests tiles from it directly. The provider sees
the viewer's IP address and which map areas are shown, which includes the areas where your
clusters are. Leave `MAP_TILE_URL` unset where that matters.
