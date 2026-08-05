# Real Estate Matcher — SQLite Variant — Architecture Document

**Scope:** this document covers the SQLite variant of the project — identical
to the main app except school ratings are stored in a real SQLite database
(`app/data/schools.db`) instead of `schools.json`. Covers the `generated`
data source (500+ synthetic listings) specifically.

Diagrams are [Mermaid](https://mermaid.js.org) — they render natively when
this file is viewed on GitHub, GitLab, and most modern markdown viewers.
Standalone `.mmd` source files are also in `mermaid/` if you want to drop
one into the [Mermaid Live Editor](https://mermaid.live) directly.

**Only two views actually changed** from the main project's architecture
doc — Logical View and Physical View, both reflecting the schools.json →
schools.db swap. Use-Case View, the Sequence Diagram, and Security View are
identical, since none of them depend on how school data happens to be
stored.

---

## 1. Use-Case View

```mermaid
flowchart LR
    Buyer([Home Buyer])
    Claude([Claude<br/>Anthropic API])
    OpenAI([GPT<br/>OpenAI API])

    subgraph System["Real Estate Matcher — generated dataset, 500+ listings"]
        UC1(Browse with hard filters<br/>price, beds, baths, sqft,<br/>HOA, stories, style, schools)
        UC2(Search with AI matching<br/>freeform preferences)
        UC3(Cancel an in-progress<br/>AI search)
        UC4(Select AI provider)
        UC5(Verify a match<br/>inspect raw listing text)
        UC6(Score a listing against<br/>buyer's preferences)
    end

    Buyer --> UC1
    Buyer --> UC2
    Buyer --> UC3
    Buyer --> UC4
    Buyer --> UC5

    UC2 -. include .-> UC6
    UC3 -. extend .-> UC2
    UC4 -. extend .-> UC6

    UC6 --> Claude
    UC6 --> OpenAI
```

---

## 2. Logical View

**The change from the main project:** `schools_service.py` is SQLite-backed
— it runs real SQL queries against `schools.db` instead of parsing
`schools.json` into memory on every startup. Everything else calling it
(`listings_service.py`, and transitively the routers) is unchanged — the
public interface (`lookup_school()`, `attach_school_ratings()`) is
identical either way.

```mermaid
flowchart TD
    Frontend["app.jsx<br/>React SPA, static"]
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

    Frontend --> ListingsRouter
    Frontend --> MatchRouter
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

---

## 3. Physical View

**The change:** `schools.db` replaces `schools.json` as a physical file on
disk. It's built once by `scripts/seed_schools_db.py` and committed to the
repo — nothing writes to it at runtime.

```mermaid
flowchart TD
    Browser["Browser<br/>HomeMatch SPA"]
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

**Why `schools.db` is safe on hosts with an ephemeral filesystem** (e.g.
Render's free tier, which wipes local file changes on every redeploy,
restart, or spin-down): it's **read-only at runtime**. Nothing in the app
ever writes to it after `seed_schools_db.py` builds it — it's committed to
the repo and rebuilt fresh on every deploy, the same way
`generated_listings.json` already is. This is a fundamentally different
situation from a runtime cache or search history, which *would* break on
this kind of host — see the main project's deployment notes for why that
distinction mattered when we considered and rejected a caching feature
earlier.

---

## 4. Sequence Diagram — AI-Matching a Search

Unchanged from the main project — school rating lookups happen inside
`listings_service.py`'s `filter_by_school_rating()` step, which internally
now hits SQLite instead of JSON, but the request/response flow at this
level of detail is identical either way.

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

---

## 5. Security View

Unchanged from the main project — this variant doesn't touch
authentication, CORS, secrets handling, or input validation at all.

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
    API -->|"HTTPS + key"| Anthropic
    API -->|"HTTPS + key"| OpenAI

    style Untrusted fill:#2a0f0f,stroke:#CC0000,stroke-width:2px
    style Trusted fill:#0f2412,stroke:#007700,stroke-width:2px
    style External fill:#1e1e1e,stroke:#888888,stroke-width:2px
```

### Security findings, in plain writing

Identical to the main project's — see there for the full table. One
addition specific to this variant:

| # | Finding | Current state |
|---|---|---|
| 7 | **`schools.db` is committed to the repo** | Same as `generated_listings.json` — synthetic, non-sensitive reference data, safe to commit. No secrets or real data stored in it. |
