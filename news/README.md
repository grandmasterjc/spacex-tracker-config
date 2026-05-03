# Nyhetspublisering

Dette katalogen serveres via GitHub Pages og brukes av StarshipTracker-iOS-appen som et lett CMS.

## URL-struktur

- Index: `https://grandmasterjc.github.io/spacex-tracker-config/news/index.json`
- Artikler: `https://grandmasterjc.github.io/spacex-tracker-config/news/articles/<slug>.md`
- Bilder: `https://grandmasterjc.github.io/spacex-tracker-config/news/images/<filnavn>.jpg`

## Slik publiserer du en ny artikkel

1. **Skriv artikkel** som markdown og legg den i `news/articles/<slug>.md`.
   - Bruk H1 (`# Tittel`) som tittel
   - Underseksjoner med `## `
   - Kursiv kilder nederst
2. **Legg hero-bilde** i `news/images/<filnavn>.jpg`. Helst landskap, minst 1200px bred.
3. **Oppdater `news/index.json`**:
   - Legg ny artikkel ØVERST i `articles`-arrayet (appen sorterer på rekkefølge)
   - Sett `published_at` (ISO 8601 UTC)
   - Sett `tier`:
     - `free` — fritt tilgjengelig for alle (vanligvis nyeste artikkel)
     - `premium` — kun Mission Control-abonnenter (gratis brukere ser preview)
   - Bump `updated_at` på toppnivå
   - Bump `version` hvis det er bryteendringer i schemaet
4. **Commit + push** til `main`. GitHub Pages publiserer automatisk innen 1–2 minutter.
5. (Valgfritt) Send push-notifikasjon — egen cron i denne reposen.

## Felt i index.json

```json
{
  "id": "unik-id",                    // brukes for "lest"-tracking i appen
  "slug": "kort-url-vennlig",         // matcher filnavn
  "title": "Tittel",
  "summary": "Ingress 1-2 setninger",
  "category": "Starship | Falcon 9 | Starlink | Bransje | Analyse",
  "published_at": "2026-05-03T10:30:00Z",
  "author": "Mission Desk",
  "reading_minutes": 5,
  "hero_image": "images/filnavn.jpg",  // relativt til base_url
  "hero_credit": "Foto: kreditering",
  "body_path": "articles/slug.md",
  "tier": "free | premium",
  "tags": ["tag1", "tag2"]
}
```

## Paywall-strategi

- Nyeste artikkel: **free** for å trekke gratis-brukere inn
- Alt eldre enn 24 timer: **premium**
- Appen viser ingress + 250 tegn for premium-artikler til ikke-abonnenter
