# HomeLens — SQLite Variant — Architecture Document

**Scope:** covers the SQLite variant — school ratings in a real SQLite
database (`app/data/schools.db`) instead of `schools.json` — against the
`generated` data source (500+ synthetic listings, all in Redwood City).

**Reflects the 3-mode search UI**: the frontend now offers three distinct,
explicit search modes instead of one combined form with a "skip AI"
checkbox:
- **Traditional** — hard filters only, zero AI involvement
- **Filters + AI** — hard filters narrow the pool, then AI scores what's left
- **AI only** — pure natural language, zero hard filters

Diagrams are [Mermaid](https://mermaid.js.org) — render natively on GitHub,
GitLab, and most modern markdown viewers. Standalone `.mmd` files are in
`mermaid/` for [mermaid.live](https://mermaid.live).

---

## 1. Use-Case View

```mermaid
flowchart LR
    Buyer([Home Buyer])
    Claude([Claude<br/>Anthropic API])
    OpenAI([GPT<br/>OpenAI API])

    subgraph System["HomeLens — generated dataset, 500+ listings"]
        UC1(Traditional search<br/>filters only, zero AI)
        UC2(Filters + AI search<br/>hard filters narrow the pool,<br/>then AI scores what's left)
        UC3(AI-only search<br/>pure natural language,<br/>zero hard filters)
        UC4(Cancel an in-progress<br/>AI search)
        UC5(Select AI provider)
        UC6(Verify a match<br/>inspect raw listing text)
        UC7(Score a listing against<br/>buyer's preferences)
    end

    Buyer --> UC1
    Buyer --> UC2
    Buyer --> UC3
    Buyer --> UC6

    UC2 -. include .-> UC7
    UC3 -. include .-> UC7
    UC4 -. extend .-> UC2
    UC4 -. extend .-> UC3
    UC5 -. extend .-> UC7

    UC7 --> Claude
    UC7 --> OpenAI
```

**UC1 (Traditional) has no relationship to UC4 (Cancel)** — deliberately.
Traditional mode is a single, quick synchronous request with no background
job to cancel; only the two AI-using modes run as cancellable jobs.

---

## 2. Logical View

```mermaid
flowchart TD
    Frontend["app.jsx<br/>React SPA, static<br/>3 modes: Traditional / Filters+AI / AI-only"]
    Main["main.py<br/>assembly"]
    ListingsRouter["listings.py<br/>router"]
    MatchRouter["match.py<br/>router"]
    ListingsService["listings_service.py"]
    MatchingService["matching_service.py"]
    SchoolsService["schools_service.py<br/>SQLite-backed"]
    GeneratedData[("generated_listings.json<br/>500+ listings")]
    SchoolsData[("schools.db<br/>SQLite, real SQL queries")]
    Anthropic{{"Anthropic API"}}
    OpenAI{{"OpenAI API"}}

    Frontend -->|Traditional mode| ListingsRouter
    Frontend -->|"Filters+AI or AI-only mode"| MatchRouter
    Main -. includes .-> ListingsRouter
    Main -. includes .-> MatchRouter
    ListingsRouter --> ListingsService
    MatchRouter --> ListingsService
    MatchRouter --> MatchingService
    ListingsService --> SchoolsService
    ListingsService --> GeneratedData
    SchoolsService --> SchoolsData
    MatchingService --> Anthropic
    MatchingService --> OpenAI
```

**The component graph itself didn't change for the 3-mode UI** — the
backend already supported filter-free search (every `HardFilters` field
defaults to `None`); the new UI just exposes that capability clearly
instead of burying it behind a checkbox. Both AI-using modes route through
the same `MatchRouter`, differing only in which filter values get sent.

---

## 3. Physical View

```mermaid
flowchart TD
    Browser["Browser<br/>HomeLens SPA"]
    FrontendServer["Frontend static server<br/>python3 -m http.server :5500"]
    Backend["Backend server<br/>uvicorn app.main:app :8000"]
    GenFile[("generated_listings.json")]
    SchoolsDB[("schools.db<br/>SQLite — read-only at runtime")]
    EnvFile[[".env — secrets, gitignored"]]
    AnthropicCloud{{"Anthropic<br/>api.anthropic.com"}}
    OpenAICloud{{"OpenAI<br/>api.openai.com"}}

    Browser -->|"HTTP GET<br/>page load"| FrontendServer
    Browser -->|"HTTP fetch/XHR<br/>/listings /match/start<br/>/match/{id} /cancel"| Backend
    Backend --> GenFile
    Backend --> SchoolsDB
    Backend --> EnvFile
    Backend -->|"HTTPS + key"| AnthropicCloud
    Backend -->|"HTTPS + key"| OpenAICloud
```

Unaffected by the 3-mode UI — same routes already covered every mode's
actual traffic.

---

## 4a. Sequence Diagram — Filters + AI / AI-only Search

Both AI-using modes share this exact flow — they differ only in which
filter values are sent (AI-only always sends every filter as `null`).

```mermaid
sequenceDiagram
    actor Buyer
    participant FE as Frontend
    participant Router as MatchRouter
    participant LS as listings_service
    participant MS as matching_service
    participant AI as Claude / OpenAI

    Buyer->>FE: Enter preferences, click "Find my matches"
    FE->>Router: POST /match/start
    activate Router
    Router->>LS: build_hard_filters(), fetch_listings(),<br/>normalize_listing(), filter_by_school_rating()
    LS-->>Router: candidate listings
    Router->>MS: start_match_job()
    MS->>MS: spawn background thread
    MS-->>Router: job_id, total_batches
    Router-->>FE: job_id, total_batches
    deactivate Router

    Note over MS,AI: Sliding-window concurrency —<br/>BATCH_SIZE=8, up to MAX_CONCURRENT_BATCHES in flight

    loop until all batches done or cancelled
        MS->>AI: score_batch(8 listings)
        AI-->>MS: requirements[] per listing
        MS->>MS: score = round(100 * met/total)
    end

    loop every 800ms while running
        FE->>Router: GET /match/{job_id}
        Router-->>FE: status, progress
    end

    opt Buyer clicks Cancel
        FE->>Router: POST /match/{job_id}/cancel
        Router->>MS: cancel_job()
        MS->>MS: stop submitting new batches
    end

    MS->>MS: status = done | cancelled
    FE->>Router: GET /match/{job_id} (final)
    Router-->>FE: matches[]
    FE->>FE: split into Full / Partial matches
    FE-->>Buyer: render result cards
```

## 4b. Sequence Diagram — Traditional Search

Genuinely different flow — synchronous, no background job, no AI provider
contacted at all.

```mermaid
sequenceDiagram
    actor Buyer
    participant FE as Frontend
    participant Router as ListingsRouter
    participant LS as listings_service

    Buyer->>FE: Set filters, click "Find my matches"<br/>(Traditional mode — no preferences text shown)
    FE->>Router: POST /listings
    activate Router
    Router->>LS: build_hard_filters(), fetch_listings(),<br/>normalize_listing(), filter_by_school_rating()
    LS-->>Router: filtered listings
    Router-->>FE: listings[] (no match_score, no AI reasoning)
    deactivate Router
    FE-->>Buyer: render result cards<br/>(single list, no Full/Partial split — no score to split by)

    Note over FE,Router: One request, one response — no job_id,<br/>no polling loop, no AI provider ever contacted.<br/>This is the entire flow for Traditional mode.
```

---

## 5. Security View

```mermaid
flowchart LR
    subgraph Untrusted["Untrusted zone"]
        Browser["Browser / SPA"]
    end

    subgraph Trusted["Trusted zone — server process"]
        API["FastAPI app"]
        Validators["Pydantic validators"]
        Secrets[".env secrets"]
        Jobs["In-memory job registry"]
    end

    subgraph External["External trusted providers"]
        Anthropic{{"Anthropic API"}}
        OpenAI{{"OpenAI API"}}
    end

    Browser -->|HTTP| API
    API --> Validators
    API --> Secrets
    API --> Jobs
    API -->|"HTTPS + key<br/>(Filters+AI / AI-only modes only)"| Anthropic
    API -->|"HTTPS + key<br/>(Filters+AI / AI-only modes only)"| OpenAI

    style Untrusted fill:#2a0f0f,stroke:#CC0000,stroke-width:2px
    style Trusted fill:#0f2412,stroke:#007700,stroke-width:2px
    style External fill:#1e1e1e,stroke:#888888,stroke-width:2px
```

### Security findings, in plain writing

| # | Finding | Current state | Real risk if deployed publicly, unaddressed |
|---|---|---|---|
| 1 | **No authentication on any endpoint** | Confirmed — zero auth code anywhere in `app/routers/` | Anyone reaching the port can trigger real AI API costs, or poll/cancel any job by guessing its UUID |
| 2 | **CORS defaults to `*`** | `CORS_ALLOW_ORIGINS` defaults to wildcard in `config.py` | Any website's JS could call this API from a visitor's browser |
| 3 | **A secret in frontend code is not a real barrier** | N/A — general principle | Anyone can view it via browser DevTools and replay requests directly, bypassing the UI entirely |
| 4 | **Secrets handling** | `.env` gitignored, never returned in responses, never logged (only usage counts) | Verified correct |
| 5 | **Input validation** | Every field validated by Pydantic (type, enum membership) | Verified correct — invalid input gets a clean 422 |
| 6 | **Data sensitivity** | Entirely synthetic — fictional addresses, fictional school names/ratings | No real PII or real-world claims at risk |
| 7 | **`schools.db` is committed to the repo** | Same as `generated_listings.json` — synthetic, non-sensitive reference data, safe to commit | No secrets or real data stored in it |
| 8 | **Traditional mode has a stronger privacy profile** | Never contacts Anthropic or OpenAI at all — confirmed structurally, not just by convention (`ListingsRouter` never imports `matching_service`) | A buyer using Traditional mode has a real, verifiable guarantee that their search criteria never leaves the server to a third party |

---

## 6. Proposed Enhancement — Semantic Retrieval Pre-Filter (Pinecone)

**Status: design discussion only. Nothing in this section is implemented.**
No Pinecone dependency, config, or code exists anywhere in this repo. This
section exists so the idea isn't lost, and so a future session (or another
developer) can pick it up without re-deriving the reasoning from scratch.

### The problem this would solve

Today, every listing that survives hard filtering gets sent to the LLM for
full per-requirement reasoning (§4a) — for the `generated` dataset, that's
up to 500 listings across ~64 batches. The README already documents this
as a real, known cost/latency concern ("60+ calls per search... real
latency and cost, unlike testing against 14 listings") and recommends hard
filters as the mitigation. A semantic pre-filter would be a second lever,
usable even in **AI-only mode**, where there are no hard filters to narrow
anything.

### Where it would sit

A new stage between `listings_service.fetch_listings()` (hard filters) and
`matching_service`'s batch scoring — narrowing the candidate pool by
semantic similarity to the buyer's freeform preferences *before* the
expensive, accurate-but-costly per-requirement LLM scoring runs, rather
than replacing that scoring.

```mermaid
sequenceDiagram
    actor Buyer
    participant FE as Frontend
    participant Router as MatchRouter
    participant LS as listings_service
    participant VS as vector_service (PROPOSED)
    participant PC as Pinecone (PROPOSED)
    participant MS as matching_service
    participant AI as Claude / OpenAI

    Buyer->>FE: Enter preferences, click "Find my matches"
    FE->>Router: POST /match/start
    Router->>LS: build_hard_filters(), fetch_listings(), normalize_listing()
    LS-->>Router: hard-filtered candidates (e.g. 500)
    Router->>VS: semantic_prefilter(preferences, candidates)
    VS->>PC: embed(preferences), query top-K by similarity
    PC-->>VS: top-K mls_ids (e.g. 75 of 500)
    VS-->>Router: narrowed candidate list
    Router->>MS: start_match_job(narrowed candidates)
    Note over MS,AI: Unchanged from today — same batch-scoring<br/>pipeline, just fed a smaller, pre-filtered pool
```

A separate, offline/on-data-change indexing path would embed each
listing's `description` (+ maybe a compact structured summary) and upsert
it into Pinecone with `mls_id` as metadata — this runs once per dataset
change, not per search, similar in spirit to how `seed_schools_db.py`
already runs once per `schools.json` change rather than on every request.

### Why this, specifically, over other options

- **Doesn't touch the accuracy story.** The LLM still reads full listing
  text and does the same honest, per-requirement `met: true/false`
  judgment (§10) — Pinecone only decides *which* listings reach that
  step, never scores anything itself. The "AI reads real listing text
  instead of pattern-matching keywords" claim in §6 of the handoff
  doesn't weaken.
- **Composes with, doesn't replace, hard filters.** Filters + AI mode
  would run hard filters first (as today), then semantic pre-filter the
  survivors. AI-only mode — which today sends the entire dataset to the
  LLM with zero narrowing — is where this would help most.
- **Matches the existing warm-up-batch philosophy.** §9's warm-up batch
  exists purely to make a search feel faster without changing what gets
  scored; a semantic pre-filter is the same instinct applied earlier in
  the pipeline, at real cost savings instead of just perceived latency.

### Honest tradeoffs — why this isn't a clear "yes, build it"

- **A third AI cost line.** An embedding call per listing at index time,
  and one per search at query time — on top of Claude/OpenAI matching
  cost, not instead of it.
- **New external dependency.** A Pinecone account, index, and API key to
  provision and keep secret, alongside Anthropic/OpenAI.
- **The three data sources don't behave the same way here.** `generated`
  (500, static) is the only source where pre-filtering meaningfully pays
  off. `realistic` (14 listings) gains nothing — the whole point of that
  dataset is being small and fixed for ground-truth testing (§13 of the
  handoff's Tier 3 test). `live` (real SimplyRETS sandbox, content can
  change day to day per the README) would need either per-request
  embedding or a scheduled reindex job — real added complexity, not a
  drop-in.
- **Scale mismatch, honestly stated.** Vector retrieval earns its keep at
  thousands+ of listings; at 500, a full-corpus LLM pass is slow-ish and
  costs real money, but isn't actually broken. The genuine justification
  is "this is the architecture you'd want if the real dataset were 10k+
  listings" — a legitimate thing to demonstrate for a POC being
  evaluated on architectural thinking, but worth stating plainly rather
  than implying it solves an urgent problem at current scale.

### If this moves forward

A natural next step, still without writing code, would be sketching the
`vector_service.py` interface (`index_listings()`, `semantic_prefilter()`)
and deciding the top-K cutoff and embedding model as their own explicit
decisions — the same "verify claims against real data before implementing"
pattern already established for this project (handoff §20), applied to
measuring actual score-quality impact of pre-filtering before committing
to a K value, the same way `analyze_scores.py` measures real distributions
before `SCORE_THRESHOLD` gets set.
