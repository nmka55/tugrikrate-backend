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
and serves them over one stable contract. The app must **only** call
this service — never a bank directly.

- Source: https://github.com/nmka55/tugrikrate-backend
- Running locally right now at `http://192.168.0.143:8000`
  (LAN address, for a physical device)
- From the iOS **Simulator**, use `http://127.0.0.1:8000` instead —
  the simulator shares the Mac's network stack.
- Health check: `GET /api/health` → `{"status":"healthy","version":"2.0.0"}`
- Interactive docs: `http://192.168.0.143:8000/` (Swagger UI)

The LAN IP is DHCP-assigned and can change. If requests fail, re-check
it with `ipconfig getifaddr en0` on the Mac and confirm both devices
are on the same Wi-Fi.

## The endpoint

`GET /v1/rates` — every source's latest rates. Optional filters:
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

**10. Timestamps are UTC with a `Z` suffix.** Use an ISO8601 decoding
strategy; `.iso8601` works for these.

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
   configuration (Debug → the local address, Release → a placeholder
   for the future production URL), `async/await`, typed errors.
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
