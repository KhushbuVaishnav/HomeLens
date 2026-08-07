const { useState, useRef, useEffect } = React;

// Point this at wherever your FastAPI backend is running.
// Auto-detects local dev vs deployed. Locally, talks to your local
// backend. When deployed, talks to your real Render backend below.
const API_BASE =
  window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1"
    ? "http://127.0.0.1:8000"
    : "https://homelens-backend-dvve.onrender.com";

// Empty form input -> null (not sent as a filter), otherwise -> Number.
// Used repeatedly below instead of repeating "value ? Number(value) : null" per field.
function numOrNull(value) {
  return value ? Number(value) : null;
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

function ResultCard({ listing, isPartial }) {
  const [expanded, setExpanded] = useState(false);
  const price = listing.price
    ? `$${listing.price.toLocaleString()}`
    : "Price n/a";
  const hasMatchData = typeof listing.match_score === "number";

  return (
    <div className={`result-card${isPartial ? " result-card--partial" : ""}`}>
      {hasMatchData ? (
        <MatchGauge score={listing.match_score} isPartial={isPartial} />
      ) : (
        <div className="match-gauge match-gauge--empty">
          <span className="match-gauge__no-score">—</span>
        </div>
      )}
      <div className="result-card__body">
        <p className="result-card__address">{listing.address || "Address unavailable"}</p>
        <p className="result-card__location">{listing.city}{listing.city && listing.state ? ", " : ""}{listing.state}</p>

        <div className="spec-row">
          <span className="spec-row__item"><strong>{price}</strong></span>
          <span className="spec-row__item"><strong>{listing.beds ?? "—"}</strong> bd</span>
          <span className="spec-row__item"><strong>{listing.baths ?? "—"}</strong> ba</span>
          <span className="spec-row__item"><strong>{listing.sqft ? listing.sqft.toLocaleString() : "—"}</strong> sqft</span>
          {listing.stories && (
            <span className="spec-row__item"><strong>{listing.stories}</strong> {listing.stories === 1 ? "story" : "stories"}</span>
          )}
          {listing.style && (
            <span className="spec-row__item"><strong>{listing.style}</strong></span>
          )}
          {listing.property_type && (
            <span className="spec-row__item"><strong>{listing.property_type === "Condominium" ? "Condo" : "Single family"}</strong></span>
          )}
          {listing.hoa_fee ? (
            <span className="spec-row__item">HOA <strong>${listing.hoa_fee}</strong>/mo</span>
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

        <button
          type="button"
          className="expand-toggle"
          onClick={() => setExpanded((e) => !e)}
        >
          {expanded ? "Hide full listing details ▲" : "Verify — view full listing details ▼"}
        </button>

        {expanded && (
          <div className="result-card__expanded">
            {listing.photos && listing.photos.length > 0 && (
              <div className="result-card__photos">
                {listing.photos.map((url, i) => (
                  <img
                    key={i}
                    src={url}
                    alt={`${listing.address || "Listing"} photo ${i + 1}`}
                    className="result-card__photo"
                    onError={(e) => { e.target.style.display = "none"; }}
                  />
                ))}
              </div>
            )}
            <p className="result-card__expanded-label">Full description (verify Claude's quote against this directly)</p>
            <p className="result-card__expanded-description">{listing.description || "No description available."}</p>

            <div className="result-card__expanded-meta">
              <span><strong>MLS ID:</strong> {listing.mls_id}</span>
              <span><strong>Year built:</strong> {listing.year_built ?? "—"}</span>
              <span><strong>Lot size:</strong> {listing.lot_size ? `${listing.lot_size} sqft` : "—"}</span>
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
  const [status, setStatus] = useState("idle"); // idle | loading | error | done
  const [errorMessage, setErrorMessage] = useState("");
  const [validationError, setValidationError] = useState(""); // client-side form issues — never touches status/results
  const [results, setResults] = useState([]);
  const [progress, setProgress] = useState({ completed: 0, total: 0, inFlight: 0 });
  const [retryCount, setRetryCount] = useState(0);
  const [failedBatches, setFailedBatches] = useState(0);
  const [wasCancelled, setWasCancelled] = useState(false);
  const abortControllerRef = useRef(null); // used for the quick /listings (Browse all) call
  const jobIdRef = useRef(null);           // used for the background AI-matching job
  const pollingActiveRef = useRef(false);  // lets Cancel/Reset stop an in-progress poll loop
  const [backendMeta, setBackendMeta] = useState(null);
  const [selectedDataSource, setSelectedDataSource] = useState(null);
  const [selectedAiProvider, setSelectedAiProvider] = useState(null);

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
    setFailedBatches(0);
    setWasCancelled(false);
  }

  async function handleSubmit(e) {
    e.preventDefault();

    if (!skipAI && !preferences.trim()) {
      setValidationError("Describe what you're looking for — even a few phrases helps the matching. Or switch to Traditional mode above to search with filters only, no AI.");
      return;
    }
    setValidationError("");

    setStatus("loading");
    setErrorMessage("");
    setResults([]); // clear stale results from the previous search immediately
    setProgress({ completed: 0, total: 0, inFlight: 0 });
    setRetryCount(0);
    setFailedBatches(0);
    setWasCancelled(false);

    const controller = new AbortController();
    abortControllerRef.current = controller;
    jobIdRef.current = null;

    // In AI-only mode, the filter fields are hidden but filters state still
    // holds its defaults underneath (e.g. cities: "Redwood City") — without
    // this override, that stale default would silently still apply as a
    // hard filter, breaking the promise that this mode uses NO hard
    // filters at all, purely natural language.
    const filterBody = searchMode === "nlp_only" ? {
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
        // Browse-all mode: hits /listings, hard filters only, no Claude call
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
        return;
      }

      // AI-matching mode: start a background job, then poll for progress.
      // This is what makes Cancel actually stop further Claude/OpenAI calls,
      // instead of just abandoning the browser's wait on one big request.
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
        setFailedBatches(data.failed_batches || 0);
        setResults(data.matches || []); // progressive — updates every poll, not just at completion

        if (data.status === "done" || data.status === "cancelled") {
          setWasCancelled(data.status === "cancelled");
          setStatus("done");
          return;
        }
        await new Promise((r) => setTimeout(r, 800)); // poll interval
      }
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

  return (
    <React.Fragment>
      <header className="title-block">
        <div className="title-block__inner">
          <div className="title-block__mark">Home<span>Lens</span> - AI that sees a home through your <span>lens</span>!</div>
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
              Traditional
              <span className="mode-selector__hint">Filters only, no AI</span>
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={searchMode === "ai_assisted"}
              className={`mode-selector__option${searchMode === "ai_assisted" ? " mode-selector__option--active" : ""}`}
              onClick={() => setSearchMode("ai_assisted")}
            >
              Filters + AI
              <span className="mode-selector__hint">Narrow, then let AI match</span>
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={searchMode === "nlp_only"}
              className={`mode-selector__option${searchMode === "nlp_only" ? " mode-selector__option--active" : ""}`}
              onClick={() => setSearchMode("nlp_only")}
            >
              AI only
              <span className="mode-selector__hint">Just describe it</span>
            </button>
          </div>

          {searchMode !== "nlp_only" && (
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
                <React.Fragment><span className="spinner" />{skipAI ? "Loading..." : "Matching..."}</React.Fragment>
              ) : (
                skipAI ? "Browse all" : "Find my matches"
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

        <section>
          {status === "error" && (
            <div className="error-banner">
              <p className="error-banner__title">Something went wrong</p>
              <p className="error-banner__body">{errorMessage}</p>
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
                  : "Fill in your criteria and describe what you're actually looking for — the AI reads each listing's description, not just its specs, to find real fits."}
              </p>
            </div>
          )}

          {status === "loading" && (
            <div className="state-panel">
              <p className="state-panel__title">
                {skipAI ? "Loading listings..." : "Scoring listings..."}
              </p>
              <p className="state-panel__body">
                {!skipAI && progress.total > 0
                  ? `Scored ${progress.completed} of ${progress.total} batches${progress.inFlight > 0 ? ` — ${progress.inFlight} running concurrently right now` : ""}. Click Cancel any time to stop and see what's been scored so far.`
                  : "Pulling candidates, then scoring each one against what you described."}
              </p>
              {retryCount > 0 && (
                <p className="state-panel__retry-note">
                  ⚠ {retryCount} {retryCount === 1 ? "retry" : "retries"} so far due to rate limits — still working, just a bit slower than usual.
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
            // Browse-all results have no requirements/score data at all —
            // just show them as one flat list in that case.
            const hasRequirementData = !skipAI && results.some((l) => typeof l.requirements_total === "number");
            if (!hasRequirementData) {
              return (
                <div className="result-grid">
                  {results.map((listing) => (
                    <ResultCard key={listing.mls_id} listing={listing} />
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
                    <ResultCard key={listing.mls_id} listing={listing} />
                  ))}
                </div>
              </div>
            );
            const partialPanel = (
              <div>
                <h3 className="results-subheading results-subheading--partial">Partial matches — missing at least one requirement ({partialMatches.length})</h3>
                <div className="result-grid">
                  {partialMatches.map((listing) => (
                    <ResultCard key={listing.mls_id} listing={listing} isPartial />
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
