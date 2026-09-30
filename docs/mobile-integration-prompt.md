# Mobile integration prompt

Paste everything below the line into a Claude Code session opened in
the **iOS app** repo. It is written to be self-contained — it does not
assume the reader has seen the backend.

Keep this file in sync when the v1 contract changes.

---

I'm building **TugrikRate**, an iOS currency converter for the
Mongolian tögrög (MNT). The backend already exists and is running; I
need the app to talk to it.

## The backend

A FastAPI service that collects exchange rates from 15 Mongolian banks
and institutions, plus an international USD table, and serves them over
a stable contract. The app must **only** call
this service — never a bank directly.

- Source: https://github.com/nmka55/tugrikrate-backend
- **Production (use this by default):**
  `https://tugrikrate-backend-service.onrender.com`
- Health check: `GET /api/health` → `{"status":"healthy","version":"2.0.0"}`
- Interactive docs: `https://tugrikrate-backend-service.onrender.com/`
  (Swagger UI); OpenAPI at `/openapi.json`
- Production requires the app key on `/v1/fx` (see rule 11); without
  it `fxratesapi` is omitted.
- It runs on a free plan: after a deploy or a long idle period the
  **first request can take up to about a minute**. Use a request
  timeout of at least 60 s and show the cached data meanwhile.

Local development is optional: a backend on your Mac is reachable at
`http://127.0.0.1:8000` from the Simulator, or at the Mac's LAN IP
(`ipconfig getifaddr en0`, port 8000) from a physical device on the
same Wi-Fi. That address is plain HTTP and needs the Debug-only ATS
exception below.

## The endpoint

`GET /v1/rates` — every Mongolian source's latest rates (`GET /v1/fx`,
the international table, is described in rule 11). Optional filters:
`?currency=USD,EUR` and `?source=khanbank,golomtbank`.

Real response (trimmed to one currency and three sources; the full
response is 15 sources, 43 currencies, ~587 quotes):

```json
{
  "schema_version": 1,
  "generated_at": "2026-09-29T03:58:37Z",
  "sources": [
    {
      "id": "khanbank",
      "name": "Khan Bank",
      "name_mn": "Хаан Банк",
      "logo_url": "https://tugrikrate-backend-service.onrender.com/static/logos/khanbank.jpg?v=99cc0762a4dc",
      "type": "commercial_bank",
      "status": "ok",
      "fetched_at": "2026-09-29T03:55:39Z",
      "published_at": null,
      "last_checked_at": "2026-09-29T03:55:39Z",
      "quotes": [
        {"currency": "USD", "channel": "cash",    "side": "buy",  "rate": "3586",    "unit_basis": "1", "verified": true},
        {"currency": "USD", "channel": "cash",    "side": "sell", "rate": "3614",    "unit_basis": "1", "verified": true},
        {"currency": "USD", "channel": "noncash", "side": "buy",  "rate": "3586",    "unit_basis": "1", "verified": true},
        {"currency": "USD", "channel": "noncash", "side": "sell", "rate": "3596",    "unit_basis": "1", "verified": true}
      ]
    },
    {
      "id": "mongolbank",
      "name": "Bank of Mongolia",
      "name_mn": "Монгол Банк",
      "logo_url": "https://tugrikrate-backend-service.onrender.com/static/logos/mongolbank.png?v=ada1dcb2791e",
      "type": "central_bank",
      "status": "ok",
      "fetched_at": "2026-09-29T03:55:45Z",
      "published_at": "2026-09-27T16:00:00Z",
      "last_checked_at": "2026-09-29T03:55:45Z",
      "quotes": [
        {"currency": "USD", "channel": "reference", "side": "reference", "rate": "3595.17", "unit_basis": "1", "verified": true}
      ]
    },
    {
      "id": "sendmn",
      "name": "SendMN",
      "name_mn": "Сэнд Эм Эн ББСБ",
      "logo_url": "https://tugrikrate-backend-service.onrender.com/static/logos/sendmn.jpg?v=135034dbbb23",
      "type": "remittance",
      "status": "ok",
      "fetched_at": "2026-09-29T03:55:42Z",
      "published_at": null,
      "last_checked_at": "2026-09-29T03:55:42Z",
      "quotes": [
        {"currency": "USD", "channel": "unspecified", "side": "buy",  "rate": "3590", "unit_basis": "1", "verified": true},
        {"currency": "USD", "channel": "unspecified", "side": "sell", "rate": "3595", "unit_basis": "1", "verified": true}
      ]
    }
  ]
}
```

## Rules the contract guarantees — please honour all of them

**1. `rate` and `unit_basis` are JSON strings, and that is deliberate.**
Decode them into `Decimal`, never `Double`. A JSON number would be
parsed as a double by `JSONDecoder` and silently lose precision — the
entire backend is built to prevent that, and it would be undone in the
last 10 metres.

```swift
// Correct
let rate = Decimal(string: quote.rate)          // "3450.50" → 3450.50 exactly

// WRONG — do not do this anywhere in the app
let rate = Double(quote.rate)!
```

Do all conversion arithmetic in `Decimal`, and format for display with
`NumberFormatter` (set `generatesDecimalNumbers = true`) or
`Decimal.formatted()`. Never round in storage, only at display time.

**2. A missing rate is absent, not null or zero.** If a source does not
publish a cash sell rate, there is simply no quote with
`channel:"cash", side:"sell"`. Never treat a missing quote as 0 — show
"not published" / "—". There is no null `rate` field to guard against.

**3. `side` is always from the *bank's* perspective.**
- `buy` = what the bank pays you for foreign currency (you're selling)
- `sell` = what the bank charges you (you're buying)

So a user converting USD → MNT gets the bank's **buy** rate, and
MNT → USD uses the bank's **sell** rate. Getting this backwards is the
single easiest way to show wrong numbers, so label the UI carefully.

**4. `channel` has four possible values.** Handle all of them; use a
non-exhaustive enum with an `unknown` fallback so a future value can't
crash the app.

| Value | Meaning |
| --- | --- |
| `cash` | Physical cash rate |
| `noncash` | Transfer / card / account rate |
| `reference` | Official rate, no spread. Bank of Mongolia only — `side` is also `reference`. Show as a benchmark, never as something a user can transact at. |
| `unspecified` | The source publishes one buy/sell pair and never states which channel. **Not a guess and not an error.** M Bank, SendMN and Naiman Sharga are in this category. Display it plainly (e.g. "rate" with no channel qualifier) rather than assuming cash. |

**5. `status` is `ok` \| `stale` \| `failing`.** A `stale` or `failing`
source **still returns its last good quotes** — don't hide them, flag
them. `stale` means either we haven't reached the source recently or
the source itself is serving rates it published days ago. Show the age
using `fetched_at` / `published_at`.

**6. `verified: false` means the quoting *unit* is unconfirmed.** Treat
those rates as indicative and mark them in the UI. Currently this is
only precious metals (XAU/XAG and the non-ISO spellings some banks use)
and KPW. Every ordinary fiat currency is `verified: true` with
`unit_basis: "1"`.

**7. `unit_basis` is how many units the rate prices.** Always `"1"`
today, but don't hardcode that — the conversion is
`mnt = amount * (rate / unit_basis)`.

**8. `published_at` is nullable and is *not* fetch time.** It is only
set when the source itself states when it published. Null means the
source doesn't say — don't substitute `fetched_at` for it.

**9. Every source has an English `name` and a Mongolian `name_mn`.**
Show `name_mn` when the app language is Mongolian and `name`
otherwise. Both are always present and non-empty. They are the
institutions' own official names, so do not re-translate or "tidy"
them (e.g. TransBank is `Тээвэр Хөгжлийн Банк` in Mongolian, not a
phonetic `Транс Банк`).

**10. `logo_url` is an absolute URL, or `null`.** Load it with
`AsyncImage`/`URLSession` and cache it hard: the URL embeds a content
hash (`?v=`), so a changed logo arrives as a new URL and an unchanged
one never needs revalidating. Images are PNG or JPEG (never SVG),
mostly 512×512 squares (Bank of Mongolia 180, CK Bank 128). `null`
means no logo of verified provenance exists (Naiman Sharga today) -
show a neutral placeholder, and never derive one from the name. Treat
the images as the institutions' trademarks: show them only next to that
institution's own name and rates.

**11. Conversion policy - the app implements this, exactly.** It is the
project owner's requirement, and **this section is its only
specification**: the backend deliberately does no conversion and no
fallback, it only serves the two feeds below. Do not substitute another
source for a step, and do not expect a `/convert` endpoint.

Two endpoints supply the inputs:

- `GET /v1/rates` - Mongolian rates: `rate` is **MNT per `unit_basis`
  units** of `currency`.
- `GET /v1/fx` - the international tables: `base` is `"USD"` and each
  `rate` is **units of `currency` per 1 USD** (no MNT, no `channel`, no
  `unit_basis`). Fetched four times a day (00/06/12/18 Ulaanbaatar), so
  poll it at most every 30 minutes with `If-None-Match`; a
  `published_at` up to ~4 days old is normal over a weekend and still
  `ok`. **Never treat an `/v1/fx` rate as MNT** - the two endpoints are
  separate precisely so the units cannot be confused.

  **Send the app key on every `/v1/fx` request:**
  `X-App-Key: <value>` (the value comes from the backend owner; keep it
  out of source control, e.g. in an `.xcconfig` excluded from git, and
  inject it at build time). `/v1/fx` can carry two sources:

  | id | Sent when | What it is |
  | --- | --- | --- |
  | `frankfurter` | always | Central-bank reference, ~160 currencies, ~5 significant digits, a daily figure |
  | `fxratesapi` | **only with a valid `X-App-Key`** | Market-derived mid rate, ~154 currencies, 10 decimals, stamped to the minute |

  Without the key (or with a wrong one) `fxratesapi` is simply absent -
  not an error - so the app must work with whichever sources arrive.
  **During development** the backend may run without app keys
  configured, and then returns `fxratesapi` to every request; send the
  header anyway, so nothing changes when production turns the check on.
  Its licence allows showing its rates only inside this app, for the
  user's personal reference: do not let users export or share its
  numbers, and do not send them to any other service. The two sources
  differ slightly (KZT 439.76 vs 440.98 on the same day) - that is two
  methods, not a bug.

  **Choosing a source is the app's decision.** Whatever you choose, take
  *both* rates of one conversion from the *same* source. A sensible
  default: `fxratesapi` when present and `ok`, else `frankfurter` - and
  show which one was used.

**A. Foreign ↔ foreign (neither is MNT): `/v1/fx` only, pivoting
through USD.** No MNT, no bank, no Bank of Mongolia. The backend only
downloads USD-based rates (a table of every pair would be ~25,600
numbers), so every foreign pair is computed **X → USD → Y** - confirmed
by the project owner.

```swift
// perUSD(X) = units of X per 1 USD, all Decimal, from ONE source's snapshot.
func fxConvert(_ amount: Decimal, from a: String, to b: String,
               table: [String: Decimal]) -> Decimal? {
    if a == b { return amount }
    let perUSD: (String) -> Decimal? = { $0 == "USD" ? 1 : table[$0] }
    guard let ra = perUSD(a), let rb = perUSD(b) else { return nil }
    let usd = amount / ra            // X -> USD
    return usd * rb                  // USD -> Y
}
// 250 000 KZT -> CNY with KZT 441.22, CNY 6.71:
//   250_000 / 441.22 = 566.61 USD;  566.61 * 6.71 = 3801.9 CNY
```

If either currency is missing from the table, say so; never fall back
to MNT rates for a foreign pair.

**B. MNT involved (the other currency is X). Try in this order and
show the user which one was used:**

1. **The chosen bank's own quote** for X - `/v1/rates`, the quote for
   the channel the user picked. X→MNT uses the bank's **`buy`**;
   MNT→X uses its **`sell`**. Skip a side that is absent (never treat
   it as zero).
2. **Bank of Mongolia reference** (`mongolbank`, `channel: "reference"`)
   for X, used for both directions - if the chosen bank has no quote
   for X. Label the result "reference rate", not "bank rate".
3. **Bank of Mongolia via USD** - if `mongolbank` has no X either.
   With `usdRef` = Bank of Mongolia's USD reference rate (MNT per USD)
   and `perUSD` = the `/v1/fx` rate for X:

   ```swift
   // X -> MNT
   let mnt = amountX / perUSD * usdRef
   // MNT -> X
   let x   = amountMNT / usdRef * perUSD
   ```

   Label it "estimated via USD" - it is a composed figure, not a rate
   any institution quotes.

Do all arithmetic in `Decimal`; the international rates carry ~5
significant digits, so foreign results are indicative to roughly
0.01-0.05 %, fine for a converter and not for settlement. Step 2 and 3
figures are reference values: **do not present them as rates the user
can transact at.** When the user is actually exchanging cash, use a
bank's `cash` quotes.

**Open question for the owner (decide in the app):** whether step 1 → 2
happens when the *chosen* bank lacks X (assumed here) or only when *no*
bank has it. Build the step logic so that is a one-line change.

**12. `type` is non-exhaustive.** Now one of `commercial_bank`,
`central_bank`, `exchange_bureau`, `remittance`,
`international_aggregator`. Decode unknown values instead of throwing.

**13. Timestamps are UTC with a `Z` suffix.** Use an ISO8601 decoding
strategy; `.iso8601` works for these.

## OpenAPI (optional, recommended)

The backend publishes its contract as OpenAPI 3.1: live at
`GET /openapi.json`, and committed as `docs/openapi.json` in the backend
repo. **The app does not need it to work** - it only ever decodes JSON.
But you can generate the Swift models from it (e.g.
[swift-openapi-generator](https://github.com/apple/swift-openapi-generator))
instead of hand-writing DTOs, so a contract change becomes a compile
error rather than a runtime decode failure. Regenerate when the backend
bumps its version. In the schema every field is required; nullable ones
(`logo_url`, `fetched_at`, `published_at`, `last_checked_at`) are
`string | null` - a missing quote is absent from the `quotes` array, not
a null field.

## Caching — please implement this

`GET /v1/rates` returns an `ETag`. Send it back as `If-None-Match` and
you get a bare `304 Not Modified` with no body when nothing has
changed. Rates only move a few times a day, so this saves almost all of
the bandwidth on a polling app — which matters on cellular.

`URLSession` with `.reloadRevalidatingCacheData` and a shared
`URLCache` handles this mostly automatically, but verify it: confirm a
second request produces a 304 and that you serve the cached body.

Poll at most every 5 minutes in the foreground; the backend itself only
refreshes every 15 minutes during Mongolian banking hours (08:00–20:00
Asia/Ulaanbaatar) and hourly outside that, so anything faster is wasted
requests.

## Local HTTP on iOS

The local server is plain HTTP, which iOS blocks by default (App
Transport Security). For development only, add to `Info.plist`:

```xml
<key>NSAppTransportSecurity</key>
<dict>
    <key>NSAllowsLocalNetworking</key>
    <true/>
</dict>
```

`NSAllowsLocalNetworking` permits cleartext to local/LAN addresses
without disabling ATS globally — prefer it over
`NSAllowsArbitraryLoads`. On a physical device, iOS 14+ may also prompt
for local network access the first time; add
`NSLocalNetworkUsageDescription` with a short explanation if you see
that prompt.

**Gate this to Debug builds only.** Production will be HTTPS and must
not ship an ATS exception.

## What I'd like built

1. A `RatesAPI` client: base URL configurable per build
   configuration (Release and, by default, Debug →
   `https://tugrikrate-backend-service.onrender.com`; an optional Debug
   override for a local backend), `async/await`, typed errors.
2. `Codable` models matching the contract exactly — `rate` and
   `unit_basis` as `String` in the DTO, exposed as `Decimal` on the
   domain model. Non-exhaustive enums for `channel`, `side`, `status`
   and `type` so unknown future values decode rather than throw.
3. ETag/304 caching as described, plus last-good-response persistence
   so the app opens with data offline.
4. A small conversion layer: given an amount, a currency, a direction
   and a chosen source+channel, return the MNT value as `Decimal`.
   Cover the buy/sell direction in unit tests — that is the most
   likely place to introduce a silent bug.
5. Unit tests against the real sample JSON above, including: a missing
   quote is absent (not zero), a `reference` quote has no buy/sell, an
   `unspecified` channel decodes correctly, and precision survives
   (`"3595.17"` must not become `3595.1700000000001`).

Start by reading the repo to see what already exists, then propose a
plan before writing code. Ask me if anything about the contract is
ambiguous rather than guessing.
