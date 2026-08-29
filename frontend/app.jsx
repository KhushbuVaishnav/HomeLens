const { useState, useRef, useEffect } = React;

// Point this at wherever your FastAPI backend is running: local dev, or the
// single live GCP deployment this frontend is served alongside.
const GCP_MAIN_BACKEND = "https://homelens-550088102949.europe-west1.run.app";

const API_BASE = (() => {
  const hostname = window.location.hostname;
  if (hostname === "localhost" || hostname === "127.0.0.1") {
    return "http://127.0.0.1:8000";
  }
  return GCP_MAIN_BACKEND;
})();

// Empty form input -> null (not sent as a filter), otherwise -> Number.
// Used repeatedly below instead of repeating "value ? Number(value) : null" per field.
function numOrNull(value) {
  return value ? Number(value) : null;
}

// Composes the retry-reason phrase for the in-progress-search banner from
// the backend's {"rate limit": N, "connection error": N} breakdown.
// Deliberately NOT hardcoded to "due to rate limits" -- retries can now
// happen for either reason (see matching_service.py's _retry_with_backoff),
// so a fixed label would sometimes just be wrong about why the search is
// running slower than usual.
function formatRetryReasons(reasons) {
  const entries = Object.entries(reasons || {}).filter(([, count]) => count > 0);
  if (entries.length === 0) return "due to rate limits"; // fallback only; shouldn't normally trigger if retryCount > 0
  if (entries.length === 1) {
    const [reason] = entries[0];
    return `due to ${reason}s`; // "rate limit" -> "rate limits", "connection error" -> "connection errors"
  }
  const parts = entries.map(([reason, count]) => `${count} ${reason}${count === 1 ? "" : "s"}`);
  return `(${parts.join(", ")})`;
}

const DATA_SOURCE_LABELS = {
  live: "SimplyRETS (Live, ~45 listings)",
  realistic: "Small Dataset (14 listings)", // not shown in the UI dropdown (filtered out) — kept for scripts/verify_test_cases.py and any direct API use
  generated: "Static JSON Data (500+ listings)",
};

// POC scope note only — the static sources are small, fixed datasets
// covering exactly these cities (verified directly against the actual
// data files, not assumed). "live" is a real third-party sandbox API and
// isn't a fixed list — its coverage can change independently of this app.
const DATA_SOURCE_CITIES = {
  generated: "Redwood City",
  realistic: "Redwood City",
};

// Separate from DATA_SOURCE_CITIES above on purpose — that one is only for
// sources we've directly verified are fixed/guaranteed. This one just picks
// a sensible starting value for the City field, including a best-effort
// default for "live" (Houston, as of our last check) even though that
// source isn't guaranteed to stay that way.
const DATA_SOURCE_DEFAULT_CITY = {
  generated: "Redwood City",
  realistic: "Redwood City",
  live: "Houston",
};

// Accurate per-source description — deliberately NOT one blanket "this is
// all synthetic" statement, since that would be factually wrong for
// "live": that source is real third-party data from SimplyRETS' public
// sandbox API, not something created for this project at all.
const DATA_SOURCE_NOTES = {
  generated: "500+ synthetically generated listings for this project — not real property listings.",
  realistic: "14 hand-written listings for this project — not real property listings.",
  live: "This makes a real REST call to SimplyRETS' public Sandbox API — a demo environment we're using for this POC. Swap it for their production API and this same integration would return real, current listings. As of our last check, the Sandbox's listings were all in Houston, TX — but unlike our own fixed data, this is a live third-party feed and its coverage could change without any action on our part.",
};

const AI_PROVIDER_LABELS = {
  anthropic: "Claude",
  openai: "OpenAI",
  vertex: "Gemini",
};

// Labels for Smart search's routing badge — keyed by the same route strings
// router_service.decide_route() returns, so no separate mapping logic is
// needed anywhere else.
const ROUTE_LABELS = {
  traditional: "Traditional",
  vector: "Vector search",
  match: "AI-scored matching",
};

const DEFAULT_FILTERS = {
  cities: "Redwood City",
  minPrice: "",
  maxPrice: "",
  minBeds: "",
  minBaths: "",
  minSqft: "",
  minSchoolRating: "",
  strictSchoolRating: false,
  propertyType: "any",
  maxHoa: "",
  stories: "any", // "any" | "1" | "2plus"
  excludeRanch: false,
};

function MatchGauge({ score, isPartial }) {
  // Partial-match cards always show amber for the score itself, regardless
  // of the numeric tier — consistent with the card's amber left-border
  // accent. Reuses the existing --mid tier classes (already amber) rather
  // than adding a redundant duplicate color.
  const tier = isPartial ? "mid" : score >= 80 ? "high" : score >= 60 ? "mid" : "low";
  const scoreClass = tier === "high" ? "match-gauge__score--high" : tier === "mid" ? "match-gauge__score--mid" : "";
  const fillClass = tier === "high" ? "match-gauge__fill--high" : tier === "mid" ? "match-gauge__fill--mid" : "";

  return (
    <div className="match-gauge">
      <div className={`match-gauge__score ${scoreClass}`}>{score}</div>
      <div className="match-gauge__track">
        <div
          className={`match-gauge__fill ${fillClass}`}
          style={{ width: `${score}%` }}
        />
      </div>
      <div className="match-gauge__ticks">
        <span>0</span>
        <span>50</span>
        <span>100</span>
      </div>
    </div>
  );
}

function ResultCard({ listing, isPartial, matchedByLabel, isTraditional = false }) {
  const [expanded, setExpanded] = useState(false);
  const isVector = typeof listing.similarity === "number"; // derived from data shape, same pattern as hasMatchData below — not a separate prop the caller has to remember to pass

  const price = listing.price
    ? `$${listing.price.toLocaleString()}`
    : "Price n/a";

  const hasMatchData = typeof listing.match_score === "number";

  return (
    <div
      className={`result-card${isPartial ? " result-card--partial" : ""}${isTraditional ? " result-card--traditional" : ""}${isVector ? " result-card--vector" : ""}`}
    >
      {!isTraditional && (
        hasMatchData ? (
          <MatchGauge score={listing.match_score} isPartial={isPartial} />
        ) : (
          <div className="match-gauge match-gauge--empty">
            <span className="match-gauge__no-score">—</span>
          </div>
        )
      )}

      <div className="result-card__body">
        <p className="result-card__address">
          {listing.address || "Address unavailable"}
        </p>

        <p className="result-card__location">
          {listing.city}
          {listing.city && listing.state ? ", " : ""}
          {listing.state}
        </p>

        <div className="spec-row">
          <span className="spec-row__item">
            <strong>{price}</strong>
          </span>

          <span className="spec-row__item">
            <strong>{listing.beds ?? "—"}</strong> bd
          </span>

          <span className="spec-row__item">
            <strong>{listing.baths ?? "—"}</strong> ba
          </span>

          <span className="spec-row__item">
            <strong>
              {listing.sqft ? listing.sqft.toLocaleString() : "—"}
            </strong>{" "}
            sqft
          </span>

          {listing.stories && (
            <span className="spec-row__item">
              <strong>{listing.stories}</strong>{" "}
              {listing.stories === 1 ? "story" : "stories"}
            </span>
          )}

          {listing.style && (
            <span className="spec-row__item">
              <strong>{listing.style}</strong>
            </span>
          )}

          {listing.property_type && (
            <span className="spec-row__item">
              <strong>
                {listing.property_type === "Condominium"
                  ? "Condo"
                  : "Single family"}
              </strong>
            </span>
          )}

          {listing.hoa_fee ? (
            <span className="spec-row__item">
              HOA <strong>${listing.hoa_fee}</strong>/mo
            </span>
          ) : null}
        </div>

        {listing.school_ratings && (
          <div className="spec-row">
            {Object.entries(listing.school_ratings).map(([level, info]) => (
              <span className="spec-row__item" key={level}>
                {level}: <strong>{info.rating ?? "—"}/10</strong>
              </span>
            ))}
          </div>
        )}

        {hasMatchData && (
          <div className="result-card__reason">
            <strong>
              Why it matches
              {listing.requirements_total > 0 && (
                <span className="requirements-badge">
                  {" "}— {listing.requirements_met}/{listing.requirements_total} requirements met
                </span>
              )}
            </strong>

            {listing.match_reason}
          </div>
        )}

        {typeof listing.similarity === "number" && (
          <div className="result-card__similarity">
            <strong>{Math.round(listing.similarity * 100)}% description similarity</strong>
            {" "}— ranked by embedding distance, not AI-verified. Read the full description yourself to confirm.
          </div>
        )}

        {!isTraditional && (
          <button
            type="button"
            className="expand-toggle"
            onClick={() => setExpanded((e) => !e)}
          >
            {expanded
              ? "Hide full listing details ▲"
              : "Verify — view full listing details ▼"}
          </button>
        )}

        {(isTraditional || expanded) && (
          <div
            className={`result-card__expanded${isTraditional ? " result-card__expanded--traditional" : ""}`}
          >
            {listing.photos && listing.photos.length > 0 && (
              <div className="result-card__photos">
                {listing.photos.map((url, i) => (
                  <img
                    key={i}
                    src={url}
                    alt={`${listing.address || "Listing"} photo ${i + 1}`}
                    className="result-card__photo"
                    onError={(e) => {
                      e.target.style.display = "none";
                    }}
                  />
                ))}
              </div>
            )}

            <p className="result-card__expanded-label">
              {isTraditional
                ? "Description"
                : typeof listing.similarity === "number"
                ? "Full description (this is what the embedding was compared against)"
                : `Full description (verify ${matchedByLabel || "the AI"}'s quote against this directly)`}
            </p>

            <p className="result-card__expanded-description">
              {listing.description || "No description available."}
            </p>

            <div className="result-card__expanded-meta">
              <span>
                <strong>MLS ID:</strong> {listing.mls_id}
              </span>

              <span>
                <strong>Year built:</strong> {listing.year_built ?? "—"}
              </span>

              <span>
                <strong>Lot size:</strong>{" "}
                {listing.lot_size ? `${listing.lot_size} sqft` : "—"}
              </span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function App() {
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [preferences, setPreferences] = useState("");
  const [searchMode, setSearchMode] = useState("ai_assisted"); // traditional | ai_assisted | nlp_only
  const skipAI = searchMode === "traditional";
  const isVectorMode = searchMode === "vector"; // no AI call either, but a different results shape/flow than skipAI (Traditional)
  const [status, setStatus] = useState("idle"); // idle | loading | error | done
  const [errorMessage, setErrorMessage] = useState("");
  const [validationError, setValidationError] = useState(""); // client-side form issues — never touches status/results
  const [results, setResults] = useState([]);
  const [progress, setProgress] = useState({ completed: 0, total: 0, inFlight: 0 });
  const [retryCount, setRetryCount] = useState(0);
  const [retryReasons, setRetryReasons] = useState({});
  const [failedBatches, setFailedBatches] = useState(0);
  const [wasCancelled, setWasCancelled] = useState(false);
  const abortControllerRef = useRef(null); // used for the quick /listings (Browse all) call
  const jobIdRef = useRef(null);           // used for the background AI-matching job
  const pollingActiveRef = useRef(false);  // lets Cancel/Reset stop an in-progress poll loop
  const [backendMeta, setBackendMeta] = useState(null);
  const [selectedDataSource, setSelectedDataSource] = useState(null);
  const [selectedAiProvider, setSelectedAiProvider] = useState(null);
  const [routingDecision, setRoutingDecision] = useState(null); // Smart search only: {route, reason, requirements, escalated}

  useEffect(() => {
    fetch(`${API_BASE}/`)
      .then((r) => r.json())
      .then((data) => {
        setBackendMeta(data);
        // Initialize the dropdowns to whatever .env currently defaults to —
        // after this, they're fully user-controlled and override .env
        // per-request without changing the server's actual config file.
        setSelectedDataSource(data.data_source);
        setSelectedAiProvider(data.ai_provider);
      })
      .catch(() => setBackendMeta(null)); // silently ignore — header just falls back to a generic label
  }, []);

  // Switching modes must never leave a PREVIOUS mode's results on screen
  // under the NEW mode's tab — e.g. run a search in AI-only, then click
  // Vector search without resubmitting, and the AI-scored results would
  // otherwise just sit there, rendered (wrongly) as if they were vector
  // results, since the card rendering logic keys off each result's own
  // data shape (match_score vs. similarity), not off searchMode itself.
  // Previously harmless — the original 3 modes all rendered stale
  // results the same visual way — but Vector search's genuinely
  // different card style (flat list + similarity badge, no full/partial
  // split) makes stale results from another mode look like a phantom
  // search that never actually ran. Deliberately does NOT touch
  // filters/preferences — switching tabs should keep what you typed so
  // you can compare the same query across modes, just not show another
  // mode's leftover results while you do.
  useEffect(() => {
    pollingActiveRef.current = false; // stop any in-flight poll loop from a previous mode's still-running search
    jobIdRef.current = null;
    setStatus("idle");
    setErrorMessage("");
    setResults([]);
    setProgress({ completed: 0, total: 0, inFlight: 0 });
    setRetryCount(0);
    setRetryReasons({});
    setFailedBatches(0);
    setWasCancelled(false);
    setRoutingDecision(null);
  }, [searchMode]);

  // Keep the City field matching whichever source is active — e.g. a
  // "Redwood City" search on live's Houston-only sandbox would just return
  // zero results, so auto-correcting it when the source changes is more
  // helpful than leaving a stale, wrong city sitting there. Safe to run on
  // every change including the very first one (unlike the AI-model-reset
  // effect elsewhere in this file) — there's no separate "correct initial
  // value" to preserve here, the city should always just match the
  // currently active source, from the first render onward.
  useEffect(() => {
    if (selectedDataSource && DATA_SOURCE_DEFAULT_CITY[selectedDataSource]) {
      setFilters((prev) => ({ ...prev, cities: DATA_SOURCE_DEFAULT_CITY[selectedDataSource] }));
    }
  }, [selectedDataSource]);

  // Only "realistic" and "generated" data carry a schools field at all — "live"
  // (SimplyRETS sandbox) listings have none, so the school rating filter
  // would silently do nothing on it. Disable the controls in that case
  // instead of letting someone set a value that's quietly ignored.
  const schoolDataSupported = selectedDataSource === null
    ? true // still connecting — assume supported so nothing flashes disabled-then-enabled
    : selectedDataSource === "realistic" || selectedDataSource === "generated";

  function updateField(key, value) {
    setFilters((prev) => ({ ...prev, [key]: value }));
  }

  function handleCancel() {
    if (jobIdRef.current) {
      // Tells the backend to stop queuing further batches. A batch already
      // in flight to Claude/OpenAI still finishes — can't recall a request
      // already sent — but nothing after it will be sent. Polling (already
      // running) will pick up the "cancelled" status on its own and show
      // whatever was already scored as partial results.
      fetch(`${API_BASE}/match/${jobIdRef.current}/cancel`, { method: "POST" }).catch(() => {});
    } else if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
  }

  function handleReset() {
    pollingActiveRef.current = false;
    jobIdRef.current = null;
    setFilters(DEFAULT_FILTERS);
    setPreferences("");
    setStatus("idle");
    setErrorMessage("");
    setValidationError("");
    setResults([]);
    setProgress({ completed: 0, total: 0, inFlight: 0 });
    setRetryCount(0);
    setRetryReasons({});
    setFailedBatches(0);
    setWasCancelled(false);
    setRoutingDecision(null);
  }

  // The three execution paths, extracted out of handleSubmit so Smart
  // search's dispatch step can call whichever one its routing decision
  // names, exactly like the manually-picked tabs below already do. No
  // behavior change from before this extraction — same requests, same
  // state updates.
  async function runTraditionalSearch(filterBody, controller) {
    const res = await fetch(`${API_BASE}/listings`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(filterBody),
      signal: controller.signal,
    });
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || `Request failed (${res.status})`);
    }
    const data = await res.json();
    setResults(data.listings || []);
    setStatus("done");
  }

  // Deliberately does NOT call setResults/setStatus itself — returns the
  // matches so Smart search can inspect the count first (to decide whether
  // to escalate) before showing anything. The direct Vector search tab
  // below sets them itself right after calling this.
  async function runVectorSearch(filterBody, controller) {
    const res = await fetch(`${API_BASE}/vector-search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filters: filterBody, preferences: preferences.trim() }),
      signal: controller.signal,
    });
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || `Request failed (${res.status})`);
    }
    const data = await res.json();
    return data.matches || [];
  }

  async function runMatchSearch(filterBody, controller) {
    const startRes = await fetch(`${API_BASE}/match/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        filters: filterBody,
        preferences: preferences.trim(),
        ai_provider: selectedAiProvider,
      }),
      signal: controller.signal,
    });
    if (!startRes.ok) {
      const errData = await startRes.json().catch(() => ({}));
      throw new Error(errData.detail || `Request failed (${startRes.status})`);
    }
    const startData = await startRes.json();
    jobIdRef.current = startData.job_id;
    setProgress({ completed: 0, total: startData.total_batches, inFlight: 0 });

    pollingActiveRef.current = true;
    while (pollingActiveRef.current) {
      const res = await fetch(`${API_BASE}/match/${startData.job_id}`);
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || `Status check failed (${res.status})`);
      }
      const data = await res.json();
      setProgress({ completed: data.completed_batches, total: data.total_batches, inFlight: data.in_flight_count || 0 });
      setRetryCount(data.retry_count || 0);
      setRetryReasons(data.retry_reasons || {});
      setFailedBatches(data.failed_batches || 0);
      setResults(data.matches || []); // progressive — updates every poll, not just at completion

      if (data.status === "done" || data.status === "cancelled") {
        setWasCancelled(data.status === "cancelled");
        setStatus("done");
        return;
      }
      await new Promise((r) => setTimeout(r, 800)); // poll interval
    }
  }

  // Smart search's "plan" step (classify) + deterministic dispatch +
  // single reflect/escalate step. See docs/architecture.md for the full
  // design writeup and app/services/router_service.py for the routing
  // rule this mirrors on the backend.
  async function runSmartSearch(filterBody, controller) {
    let decision;
    try {
      const res = await fetch(`${API_BASE}/smart-search/classify`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filters: filterBody, preferences: preferences.trim(), ai_provider: selectedAiProvider }),
        signal: controller.signal,
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || `Request failed (${res.status})`);
      }
      const data = await res.json();
      decision = { route: data.route, reason: data.reason, requirements: data.requirements || [], escalated: false };
    } catch (err) {
      if (err.name === "AbortError") throw err; // user-initiated cancel — let the outer catch handle it normally
      decision = {
        route: "match",
        reason: "Couldn't classify the query — defaulting to AI-scored matching.",
        requirements: [],
        escalated: false,
      };
    }
    setRoutingDecision(decision);

    if (decision.route === "traditional") {
      await runTraditionalSearch(filterBody, controller);
      return;
    }

    if (decision.route === "vector") {
      const matches = await runVectorSearch(filterBody, controller);
      if (matches.length === 0) {
        // Reflect: the chosen path came back empty — escalate once to the
        // AI-scored path rather than showing "nothing matched" for a query
        // vector search may simply have misjudged.
        setRoutingDecision({
          route: "match",
          reason: "Vector search returned no matches — escalating to AI-scored matching.",
          requirements: decision.requirements,
          escalated: true,
        });
        await runMatchSearch(filterBody, controller);
        return;
      }
      setResults(matches);
      setStatus("done");
      return;
    }

    // decision.route === "match"
    await runMatchSearch(filterBody, controller);
  }

  async function handleSubmit(e) {
    e.preventDefault();

    if (searchMode !== "traditional" && searchMode !== "smart" && !preferences.trim()) {
      setValidationError("Describe what you're looking for — even a few phrases helps the matching. Or switch to Traditional mode above to search with filters only, no AI.");
      return;
    }
    setValidationError("");

    setStatus("loading");
    setErrorMessage("");
    setResults([]); // clear stale results from the previous search immediately
    setProgress({ completed: 0, total: 0, inFlight: 0 });
    setRetryCount(0);
    setRetryReasons({});
    setFailedBatches(0);
    setWasCancelled(false);
    setRoutingDecision(null);

    const controller = new AbortController();
    abortControllerRef.current = controller;
    jobIdRef.current = null;

    // In AI-only mode, the filter fields are hidden but filters state still
    // holds its defaults underneath (e.g. cities: "Redwood City") — without
    // this override, that stale default would silently still apply as a
    // hard filter, breaking the promise that this mode uses NO hard
    // filters at all, purely natural language. Vector search shares this
    // same treatment on purpose — for the cleanest possible "AI reasoning
    // vs. embedding similarity" comparison, both start from the exact
    // same unfiltered candidate pool, driven purely by the same free text.
    const filterBody = (searchMode === "nlp_only" || searchMode === "vector") ? {
      cities: null, min_price: null, max_price: null, min_beds: null, min_baths: null,
      min_sqft: null, min_school_rating: null, strict_school_rating: null,
      property_types: null, max_hoa: null, min_stories: null, max_stories: null,
      exclude_styles: null, data_source: selectedDataSource,
    } : {
      cities: filters.cities ? filters.cities.split(",").map((c) => c.trim()).filter(Boolean) : null,
      min_price: numOrNull(filters.minPrice),
      max_price: numOrNull(filters.maxPrice),
      min_beds: numOrNull(filters.minBeds),
      min_baths: numOrNull(filters.minBaths),
      min_sqft: numOrNull(filters.minSqft),
      min_school_rating: schoolDataSupported ? numOrNull(filters.minSchoolRating) : null,
      strict_school_rating: schoolDataSupported && filters.strictSchoolRating ? true : null,
      property_types: filters.propertyType !== "any" ? [filters.propertyType] : null,
      max_hoa: numOrNull(filters.maxHoa),
      min_stories: filters.stories === "2plus" ? 2 : null,
      max_stories: filters.stories === "1" ? 1 : null,
      exclude_styles: filters.excludeRanch ? ["Ranch"] : null,
      data_source: selectedDataSource,
    };

    try {
      if (skipAI) {
        await runTraditionalSearch(filterBody, controller);
        return;
      }

      if (searchMode === "vector") {
        // Experimental vector-search mode: single synchronous call, no
        // job/polling — a brute-force similarity scan over hundreds of
        // listings is sub-millisecond work, nothing here is slow enough
        // to need the AI-mode's background-job machinery. No LLM call at
        // all in this path — see app/routers/vector_search.py.
        const matches = await runVectorSearch(filterBody, controller);
        setResults(matches);
        setStatus("done");
        return;
      }

      if (searchMode === "smart") {
        await runSmartSearch(filterBody, controller);
        return;
      }

      // ai_assisted / nlp_only: start a background job, then poll for
      // progress. This is what makes Cancel actually stop further
      // Claude/OpenAI calls, instead of just abandoning the browser's wait
      // on one big request.
      await runMatchSearch(filterBody, controller);
    } catch (err) {
      if (err.name === "AbortError") {
        setStatus("idle"); // user-initiated cancel of the Browse-all call
        return;
      }
      setStatus("error");
      setErrorMessage(
        err.message === "Failed to fetch"
          ? "Couldn't reach the API. Is uvicorn running at " + API_BASE + "?"
          : err.message
      );
    }
  }

  // Which path actually produced (or will produce) the current results —
  // for the 4 manually-picked tabs this is just a constant restating the
  // tab itself; for Smart search it's only known once the classify step
  // responds (null until then). Decouples "which tab is active"
  // (searchMode — controls which inputs show) from "which underlying path
  // is being displayed" (used below to pick the right copy/labels/data
  // interpretation for the current results, regardless of which tab
  // requested them).
  const displayRoute = searchMode === "smart"
    ? (routingDecision ? routingDecision.route : null)
    : skipAI ? "traditional" : isVectorMode ? "vector" : "match";

  return (
    <React.Fragment>
      <header className="title-block">
        <div className="title-block__inner">
          <div className="title-block__mark">
            <svg className="title-block__icon" viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
              <path d="M4 15 L16 5 L28 15" fill="none" stroke="#5b9bff" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
              <rect x="7" y="15" width="17" height="10" fill="none" stroke="#5b9bff" strokeWidth="2" strokeLinejoin="round" />
              <rect x="13.5" y="19" width="4" height="6" fill="none" stroke="#5b9bff" strokeWidth="1.5" />
              <circle cx="23" cy="21" r="6" fill="#0f1626" stroke="#e0b93a" strokeWidth="2.2" />
              <line x1="27.2" y1="25.2" x2="31" y2="29" stroke="#e0b93a" strokeWidth="2.5" strokeLinecap="round" />
            </svg>
            <span className="title-block__text">Home<span className="title-block__brand-accent">Lens</span> - AI that sees a home through your <span className="title-block__tagline-accent">lens</span>!</span>
          </div>
          <div className="title-block__meta">
            <span className="title-block__control">
              <span className="title-block__control-row">
                <strong>Property Data:</strong>
                {backendMeta ? (
                  <select
                    className="title-block__select"
                    value={selectedDataSource || ""}
                    onChange={(e) => setSelectedDataSource(e.target.value)}
                  >
                    {backendMeta.available_data_sources
                      .filter((s) => s !== "realistic") // UI-only simplification — backend/API and scripts/verify_test_cases.py still fully support it
                      .map((s) => (
                        <option key={s} value={s}>{DATA_SOURCE_LABELS[s] || s}</option>
                      ))}
                  </select>
                ) : "connecting..."}
                {selectedDataSource && DATA_SOURCE_CITIES[selectedDataSource] && (
                  <span className="title-block__scope-note">
                    — POC, {DATA_SOURCE_CITIES[selectedDataSource]} only
                  </span>
                )}
                {selectedDataSource === "live" && DATA_SOURCE_DEFAULT_CITY.live && (
                  <span className="title-block__scope-note">
                    — currently {DATA_SOURCE_DEFAULT_CITY.live}, TX
                  </span>
                )}
              </span>
            </span>
            <span className="title-block__control title-block__control--secondary">
              <span className="title-block__control-row">
                <strong>Matched by LLM Provider:</strong>
                {backendMeta ? (
                  <select
                    className="title-block__select"
                    value={selectedAiProvider || ""}
                    onChange={(e) => setSelectedAiProvider(e.target.value)}
                    disabled={skipAI}
                    title={skipAI ? "Not used in Traditional mode" : undefined}
                  >
                    {backendMeta.available_ai_providers.map((p) => (
                      <option key={p} value={p}>{AI_PROVIDER_LABELS[p] || p}</option>
                    ))}
                  </select>
                ) : "connecting..."}
              </span>
            </span>
          </div>
          {selectedDataSource && DATA_SOURCE_NOTES[selectedDataSource] && (
            <p className="title-block__source-note">
              {DATA_SOURCE_NOTES[selectedDataSource]}
            </p>
          )}
        </div>
      </header>

      <div className="layout">
        <form className="spec-panel" onSubmit={handleSubmit}>
          <p className="spec-panel__label">Search criteria</p>
          <h2 className="spec-panel__title">What are you looking for?</h2>

          <div className="mode-selector" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={searchMode === "traditional"}
              className={`mode-selector__option${searchMode === "traditional" ? " mode-selector__option--active" : ""}`}
              onClick={() => setSearchMode("traditional")}
            >
              <span className="mode-selector__option-title">Traditional</span>
              <span className="mode-selector__hint">Filters only, no AI</span>
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={searchMode === "ai_assisted"}
              className={`mode-selector__option mode-selector__option--llm${searchMode === "ai_assisted" ? " mode-selector__option--active" : ""}`}
              onClick={() => setSearchMode("ai_assisted")}
            >
              <span className="mode-selector__option-title">Filters + AI</span>
              <span className="mode-selector__hint">Narrow, then let AI match</span>
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={searchMode === "nlp_only"}
              className={`mode-selector__option mode-selector__option--llm${searchMode === "nlp_only" ? " mode-selector__option--active" : ""}`}
              onClick={() => setSearchMode("nlp_only")}
            >
              <span className="mode-selector__option-title">AI only</span>
              <span className="mode-selector__hint">Just describe it</span>
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={searchMode === "vector"}
              className={`mode-selector__option mode-selector__option--vector${searchMode === "vector" ? " mode-selector__option--active" : ""}`}
              onClick={() => setSearchMode("vector")}
            >
              <span className="mode-selector__option-title">Vector search</span>
              <span className="mode-selector__hint">Similarity search — no AI reasoning</span>
            </button>
          </div>

          {/* Smart search isn't a peer of the 4 above — it dispatches TO
              one of them, so it's set apart visually rather than crammed
              into the same grid (which was the previous, cramped 5-column
              layout). A divider makes that relationship legible at a
              glance instead of just being "tab #5". */}
          <div className="mode-selector__divider">
            <span>or let the agent choose</span>
          </div>
          <button
            type="button"
            role="tab"
            aria-selected={searchMode === "smart"}
            className={`mode-selector__option mode-selector__option--smart mode-selector__option--full${searchMode === "smart" ? " mode-selector__option--active" : ""}`}
            onClick={() => setSearchMode("smart")}
          >
            <span className="mode-selector__option-title">Agent search</span>
            <span className="mode-selector__hint">Nothing here is required — filters, text, both, or neither. The agent figures out the best way to search whatever you give it.</span>
          </button>

          {searchMode !== "nlp_only" && searchMode !== "vector" && (
            <div className="field-group">
              <div className="field field--full">
                <label htmlFor="cities">City</label>
                <input
                  id="cities"
                  type="text"
                  value={filters.cities}
                  onChange={(e) => updateField("cities", e.target.value)}
                  placeholder="Redwood City"
                />
              </div>

              <div className="field">
                <label htmlFor="minPrice">Min price</label>
                <input
                  id="minPrice"
                  type="number"
                  min="0"
                  max="100000000"
                  step="1000"
                  value={filters.minPrice}
                  onChange={(e) => updateField("minPrice", e.target.value)}
                  placeholder="No min"
                />
              </div>
              <div className="field">
                <label htmlFor="maxPrice">Max price</label>
                <input
                  id="maxPrice"
                  type="number"
                  min="0"
                  max="100000000"
                  step="1000"
                  value={filters.maxPrice}
                  onChange={(e) => updateField("maxPrice", e.target.value)}
                  placeholder="No max"
                />
              </div>

              <div className="field">
                <label htmlFor="minBeds">Min beds</label>
                <input
                  id="minBeds"
                  type="number"
                  min="0"
                  max="20"
                  step="1"
                  value={filters.minBeds}
                  onChange={(e) => updateField("minBeds", e.target.value)}
                  placeholder="Any"
                />
              </div>
              <div className="field">
                <label htmlFor="minBaths">Min baths</label>
                <input
                  id="minBaths"
                  type="number"
                  min="0"
                  max="20"
                  step="0.5"
                  value={filters.minBaths}
                  onChange={(e) => updateField("minBaths", e.target.value)}
                  placeholder="Any"
                />
              </div>

              <div className="field field--full">
                <label htmlFor="minSqft">Min sqft</label>
                <input
                  id="minSqft"
                  type="number"
                  min="0"
                  max="50000"
                  step="50"
                  value={filters.minSqft}
                  onChange={(e) => updateField("minSqft", e.target.value)}
                  placeholder="Any"
                />
              </div>

              <div className="field field--full">
                <label htmlFor="minSchoolRating">Min school rating (1-10)</label>
                <input
                  id="minSchoolRating"
                  type="number"
                  min="1"
                  max="10"
                  step="1"
                  value={filters.minSchoolRating}
                  onChange={(e) => updateField("minSchoolRating", e.target.value)}
                  placeholder={schoolDataSupported ? "Any" : "Not available for this data source"}
                  disabled={!schoolDataSupported}
                />
                {!schoolDataSupported && (
                  <p className="field-note">
                    {DATA_SOURCE_LABELS[selectedDataSource] || "This data source"} doesn't include school data.
                  </p>
                )}
              </div>

              <div className="field field--full field--checkbox">
                <label htmlFor="strictSchoolRating" className="checkbox-label">
                  <input
                    id="strictSchoolRating"
                    type="checkbox"
                    checked={filters.strictSchoolRating}
                    onChange={(e) => updateField("strictSchoolRating", e.target.checked)}
                    disabled={!schoolDataSupported}
                  />
                  Strict (every school must individually meet the minimum, not just the average)
                </label>
              </div>

              <div className="field">
                <label htmlFor="propertyType">Property type</label>
                <select
                  id="propertyType"
                  value={filters.propertyType}
                  onChange={(e) => updateField("propertyType", e.target.value)}
                >
                  <option value="any">Any</option>
                  <option value="SingleFamilyResidence">Single family</option>
                  <option value="Condominium">Condo</option>
                </select>
              </div>
              <div className="field">
                <label htmlFor="maxHoa">Max HOA / mo</label>
                <input
                  id="maxHoa"
                  type="number"
                  min="0"
                  max="20000"
                  step="10"
                  value={filters.maxHoa}
                  onChange={(e) => updateField("maxHoa", e.target.value)}
                  placeholder="Any"
                />
              </div>

              <div className="field field--full">
                <label htmlFor="stories">Number of stories</label>
                <select
                  id="stories"
                  value={filters.stories}
                  onChange={(e) => updateField("stories", e.target.value)}
                >
                  <option value="any">Any</option>
                  <option value="1">1 story (no stairs)</option>
                  <option value="2plus">2+ stories</option>
                </select>
              </div>

              <div className="field field--full field--checkbox">
                <label htmlFor="excludeRanch" className="checkbox-label">
                  <input
                    id="excludeRanch"
                    type="checkbox"
                    checked={filters.excludeRanch}
                    onChange={(e) => updateField("excludeRanch", e.target.checked)}
                  />
                  Exclude ranch-style homes
                </label>
              </div>
            </div>
          )}

          {searchMode !== "traditional" && (
            <div className="field-group">
              <div className="field field--full">
                <label htmlFor="preferences" className="field__ai-label">
                  {searchMode === "nlp_only"
                    ? "Describe, in your own words, what your ideal home looks like!"
                    : searchMode === "vector"
                    ? "Describe your ideal home — ranked by description similarity, not AI reasoning."
                    : searchMode === "smart"
                    ? "Describe your ideal home (or leave it blank and use filters only) — the agent picks the best way to search it."
                    : "Describe, in your own words, the details that make a difference!"}
                </label>
                <textarea
                  id="preferences"
                  value={preferences}
                  onChange={(e) => {
                    setPreferences(e.target.value);
                    if (validationError) setValidationError(""); // clear as soon as they start fixing it
                  }}
                  placeholder="Quiet street, updated kitchen, a spare room for a home office, not near a busy road..."
                />
                {validationError && (
                  <p className="field__hint field__hint--warn">{validationError}</p>
                )}
              </div>
            </div>
          )}

          <div className="button-row">
            <button type="submit" className="submit-btn" disabled={status === "loading"}>
              {status === "loading" ? (
                <React.Fragment><span className="spinner" />{
                  displayRoute === null ? "Routing..."
                    : displayRoute === "traditional" ? "Loading..."
                    : displayRoute === "vector" ? "Searching..."
                    : "Matching..."
                }</React.Fragment>
              ) : (
                searchMode === "smart" ? "Find my matches"
                  : skipAI ? "Browse all"
                  : isVectorMode ? "Find similar listings"
                  : "Find my matches"
              )}
            </button>
            {status === "loading" ? (
              <button type="button" className="reset-btn" onClick={handleCancel}>
                Cancel
              </button>
            ) : (
              <button type="button" className="reset-btn" onClick={handleReset}>
                Reset
              </button>
            )}
          </div>
        </form>

        <section className="results-panel">
          {status === "error" && (
            <div className="error-banner">
              <p className="error-banner__title">Something went wrong</p>
              <p className="error-banner__body">{errorMessage}</p>
            </div>
          )}

          {searchMode === "smart" && routingDecision && (
            <div className={`routing-badge${routingDecision.escalated ? " routing-badge--escalated" : ""}`}>
              <span className="routing-badge__label">
                {routingDecision.escalated ? "Escalated to" : "Routed to"} {ROUTE_LABELS[routingDecision.route] || routingDecision.route}
              </span>
              <span className="routing-badge__reason">{routingDecision.reason}</span>
            </div>
          )}

          {status !== "error" && (
            <div className="results-header">
              <h2 className="results-header__title">Matches</h2>
              {results.length > 0 && (
                <span className="results-header__count">
                  {results.length} listing{results.length === 1 ? "" : "s"}{status === "loading" ? " so far" : ""}
                </span>
              )}
            </div>
          )}

          {status === "idle" && (
            <div className="state-panel">
              <p className="state-panel__title">No search run yet</p>
              <p className="state-panel__body">
                {searchMode === "traditional"
                  ? "Set your filters and click Find my matches — straightforward search, no AI involved."
                  : searchMode === "nlp_only"
                  ? "Just describe what you're looking for below — no filters needed. The AI reads each listing's full description to find real fits."
                  : searchMode === "vector"
                  ? "Experimental: just describe what you're looking for below — ranked purely by embedding similarity to each listing's description. No AI reasoning, no filters — a comparison point for the AI-only mode, not a replacement for it."
                  : searchMode === "smart"
                  ? "Give filters, freeform text, or both — the agent breaks your description into requirements and picks whichever of Traditional, Vector search, or AI-scored matching fits best, then shows you which it picked and why."
                  : "Fill in your criteria and describe what you're actually looking for — the AI reads each listing's description, not just its specs, to find real fits."}
              </p>
            </div>
          )}

          {status === "loading" && (
            <div className="state-panel">
              <p className="state-panel__title">
                {displayRoute === null ? "Deciding how to search..."
                  : displayRoute === "traditional" ? "Loading listings..."
                  : displayRoute === "vector" ? "Ranking by similarity..."
                  : "Scoring listings..."}
              </p>
              <p className="state-panel__body">
                {displayRoute === null
                  ? "Breaking down your description to pick the best search path."
                  : displayRoute === "vector"
                  ? "Embedding your query and ranking listings by description similarity — no AI reasoning involved."
                  : displayRoute === "match" && progress.total > 0
                  ? `Scored ${progress.completed} of ${progress.total} batches${progress.inFlight > 0 ? ` — ${progress.inFlight} running concurrently right now` : ""}. Click Cancel any time to stop and see what's been scored so far.`
                  : "Pulling candidates, then scoring each one against what you described."}
              </p>
              {retryCount > 0 && (
                <p className="state-panel__retry-note">
                  ⚠ {retryCount} {retryCount === 1 ? "retry" : "retries"} so far {formatRetryReasons(retryReasons)} — still working, just a bit slower than usual.
                </p>
              )}
            </div>
          )}

          {status === "done" && wasCancelled && (
            <div className="cancelled-banner">
              {results.length > 0
                ? `Search cancelled after ${progress.completed} of ${progress.total} batches — showing partial results from what was already scored.`
                : `Search cancelled after ${progress.completed} of ${progress.total} batches — none of what was scored passed the match threshold.`}
            </div>
          )}

          {status === "done" && !wasCancelled && failedBatches > 0 && (
            <div className="cancelled-banner">
              This search is incomplete — {failedBatches} of {progress.total} batch{failedBatches === 1 ? "" : "es"} failed
              due to an AI service issue (e.g. a timeout). Results below are from everything that succeeded; try
              searching again for a chance at the listings that were missed.
            </div>
          )}

          {status === "done" && results.length === 0 && (
            <div className="state-panel">
              <p className="state-panel__title">Nothing matched</p>
              <p className="state-panel__body">
                {wasCancelled && progress.completed === 0
                  ? "Cancelled before any batch finished scoring — nothing to show yet."
                  : wasCancelled
                  ? `Cancelled after ${progress.completed} of ${progress.total} batches finished, but none of the listings scored above the match threshold.`
                  : "Try loosening your filters, or your city may have limited listings in the sandbox data."}
              </p>
            </div>
          )}

          {status !== "error" && results.length > 0 && (() => {
            // Browse-all results (Traditional) and Vector search results
            // both have no requirements/score data at all — just show them
            // as one flat list in either case. Keyed off displayRoute, not
            // searchMode/skipAI/isVectorMode directly, so this reads
            // correctly for Smart search too — its results can genuinely be
            // any of the 3 underlying shapes, decided at runtime by the
            // routing decision, not by which tab is active.
            const hasRequirementData = displayRoute === "match" && results.some((l) => typeof l.requirements_total === "number");
if (!hasRequirementData) {
  return (
    <div className="result-grid">
      {results.map((listing) => (
        <ResultCard
          key={listing.mls_id}
          listing={listing}
          isTraditional={displayRoute === "traditional"}
          matchedByLabel={
            displayRoute === "match"
              ? (AI_PROVIDER_LABELS[selectedAiProvider] || selectedAiProvider)
              : null
          }
        />
      ))}
    </div>
  );
}

            // Split into full vs. partial matches automatically — this is
            // deliberately NOT a user-configurable tolerance setting. Asking
            // someone to pre-declare "it's fine if you ignore requirement X"
            // before seeing any results doesn't make sense; showing both
            // tiers transparently and letting them judge does.
            //
            // Rendered as two side-by-side columns when both categories
            // have results — with many results, scrolling through every
            // full match just to reach the first partial one (or vice
            // versa) got tedious. But if only ONE category currently has
            // anything (common early in a search, or for a very specific
            // preference that only matches one way), a two-column split
            // wastes half the width on an empty placeholder — a single
            // full-width panel reads more clearly in that case. This is
            // evaluated live, so the layout can shift from one wide panel
            // to two columns the moment the first result of the other
            // category actually arrives — a natural consequence of
            // genuinely progressive results, not a bug.
            const fullMatches = results.filter((l) => l.requirements_total === 0 || l.requirements_met === l.requirements_total);
            const partialMatches = results.filter((l) => l.requirements_total > 0 && l.requirements_met < l.requirements_total);

            const fullPanel = (
              <div>
                <h3 className="results-subheading">Full matches — every requirement met ({fullMatches.length})</h3>
                <div className="result-grid">
                  {fullMatches.map((listing) => (
                    <ResultCard key={listing.mls_id} listing={listing} matchedByLabel={AI_PROVIDER_LABELS[selectedAiProvider] || selectedAiProvider} />
                  ))}
                </div>
              </div>
            );
            const partialPanel = (
              <div>
                <h3 className="results-subheading results-subheading--partial">Partial matches — missing at least one requirement ({partialMatches.length})</h3>
                <div className="result-grid">
                  {partialMatches.map((listing) => (
                    <ResultCard key={listing.mls_id} listing={listing} isPartial matchedByLabel={AI_PROVIDER_LABELS[selectedAiProvider] || selectedAiProvider} />
                  ))}
                </div>
              </div>
            );

            if (fullMatches.length > 0 && partialMatches.length === 0) return fullPanel;
            if (partialMatches.length > 0 && fullMatches.length === 0) return partialPanel;
            return (
              <div className="results-columns">
                {fullPanel}
                {partialPanel}
              </div>
            );
          })()}
        </section>
      </div>
    </React.Fragment>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
