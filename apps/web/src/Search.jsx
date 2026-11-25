import React, { useEffect, useState } from 'react';
import { api, pdfPage } from './api.js';
import { Highlighted } from './highlight.js';

const defaults = { state: '', department: '', category: '' };
const names = { state: 'State', department: 'Department', category: 'Category' };

const sourceLabels = {
  SCHEME_ARTICLE: 'Scheme article', SCHEME_PDF: 'Scheme information snapshot',
  OFFICIAL_PDF: 'Official document', PDF: 'PDF document', CSV: 'Scheme record',
};

function Article({ docId }) {
  const [text, setText] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  async function read() {
    if (text !== null) { setText(null); return; }
    setLoading(true);
    setError('');
    try {
      const { data } = await api('/documents/' + docId);
      if (typeof data.article_text !== 'string') throw new Error('Article unavailable.');
      setText(data.article_text);
    } catch (failure) { setError(failure.message); }
    finally { setLoading(false); }
  }

  return <div>
    <button className="secondary" type="button" onClick={read} disabled={loading} aria-expanded={text !== null}>
      {loading ? 'Loading article...' : text === null ? 'Read Article' : 'Close article'}
    </button>
    {error && <p role="alert">{error}</p>}
    {text !== null && <div className="article-text">{text}</div>}
  </div>;
}

function Results({ data, user }) {
  const [notice, setNotice] = useState('');
  async function bookmark(body) {
    try {
      await api('/bookmarks', { method: 'POST', body });
      setNotice('Bookmark saved.');
    } catch (error) { setNotice(error.message); }
  }
  return <>
    {notice && <p role="status">{notice}</p>}
    {data.results.map((hit) => (
      <article className="panel result" key={hit.passage_id}>
        <h2>{hit.title || 'Title unavailable'}</h2>
        <p><strong className="source-label">{sourceLabels[hit.source_type] ?? "Source unavailable"}</strong></p>
        <p className="meta">{hit.scheme_name || 'Scheme unavailable'}</p>
        <p><Highlighted text={hit.snippet} spans={hit.match_spans} /></p>
        <dl className="metadata">
          <div><dt>Department</dt><dd>{hit.department ?? 'Unavailable'}</dd></div>
          <div><dt>State</dt><dd>{hit.regions?.length ? hit.regions.join(', ') : hit.state ?? 'Unavailable'}</dd></div>
          <div><dt>Category</dt><dd>{hit.category ?? 'Unavailable'}</dd></div>
          <div><dt>Issue date</dt><dd>{hit.issue_date ?? 'Unavailable'}</dd></div>
          {hit.page_no != null && <div><dt>Page</dt><dd>{hit.page_no}</dd></div>}
          <div><dt>Relevance</dt><dd>{hit.score.toFixed(3)}</dd></div>
        </dl>
        {!!hit.source_references?.length && <details>
          <summary>Source references</summary>
          <ul>{hit.source_references.filter((url) => /^https?:\/\//i.test(url)).map((url) =>
            <li key={url}><a href={url} target="_blank" rel="noreferrer">{url}</a></li>)}</ul>
        </details>}
        {hit.source_type === 'SCHEME_ARTICLE' && <Article docId={hit.doc_id} />}
        <div className="actions">
          {hit.pdf_url && <a className="button secondary" href={pdfPage(hit.pdf_url, hit.page_no)} target="_blank" rel="noreferrer">Open PDF</a>}
          {user ? <>
            <button className="secondary" onClick={() => bookmark({ doc_id: hit.doc_id, page_no: hit.page_no })}>Save document</button>
            {hit.scheme_id && <button className="secondary" onClick={() => bookmark({ scheme_id: hit.scheme_id })}>Save scheme</button>}
          </> : <a href="#login">Sign in to bookmark</a>}
        </div>
      </article>
    ))}
  </>;
}

export function Search({ user, initialQuery }) {
  const [query, setQuery] = useState(initialQuery);
  const [filters, setFilters] = useState(defaults);
  const [options, setOptions] = useState(defaults);
  const [suggestions, setSuggestions] = useState([]);
  const [focused, setFocused] = useState(false);
  const [k, setK] = useState(10);
  const [data, setData] = useState(null);
  const [applied, setApplied] = useState(null);
  const [warning, setWarning] = useState(null);
  const [error, setError] = useState('');
  const [filterError, setFilterError] = useState('');
  const [loading, setLoading] = useState(false);
  const pending = React.useRef();

  async function search(text, values, page = 1, size = k) {
    if (!text.trim()) return;
    pending.current?.abort();
    const request = new AbortController();
    pending.current = request;
    setFocused(false); setSuggestions([]); setError(''); setWarning(null); setLoading(true);
    const params = new URLSearchParams({ q: text, page, k: size });
    for (const [key, value] of Object.entries(values)) if (value) params.set(key, value);
    try {
      const result = await api('/search?' + params, { signal: request.signal });
      setData(result.data); setWarning(result.warning); setApplied({ text, values });
    } catch (failure) {
      if (failure.name !== 'AbortError') { setError(failure.message); setData(null); }
    } finally {
      if (pending.current === request) setLoading(false);
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    api('/filters', { signal: controller.signal }).then(({ data }) => setOptions(data))
      .catch((failure) => { if (failure.name !== 'AbortError') setFilterError(failure.message); });
    if (initialQuery) search(initialQuery, defaults);
    return () => { controller.abort(); pending.current?.abort(); };
  }, [initialQuery]);

  useEffect(() => {
    const prefix = query.trimEnd().split(/\s+/u).at(-1);
    if (!focused || !prefix || [...prefix].length > 64) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      api('/autocomplete?' + new URLSearchParams({ prefix }), { signal: controller.signal })
        .then(({ data }) => setSuggestions(data.suggestions))
        .catch(() => { if (!controller.signal.aborted) setSuggestions([]); });
    }, 250);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [query, focused]);

  function select(term) {
    setQuery(query.trimEnd().replace(/\S+$/u, term) + ' ');
    setSuggestions([]); setFocused(false);
  }

  return <>
    <section className="hero">
      <h1>Search schemes and documents</h1>
      <p>Find the relevant passage in a government scheme document.</p>
      <form onSubmit={(event) => { event.preventDefault(); search(query, filters); }}>
        <div className="search-row">
          <div className="search-input">
            <label htmlFor="query" className="sr-only">Search query</label>
            <input id="query" value={query} required maxLength={512} autoComplete="off"
              placeholder="Search by keyword, phrase or Boolean query"
              onChange={(event) => { setQuery(event.target.value); setSuggestions([]); setFocused(true); }}
              onFocus={() => setFocused(true)} onBlur={(event) => {
                if (!event.currentTarget.parentElement.contains(event.relatedTarget)) setFocused(false);
              }} onKeyDown={(event) => { if (event.key === 'Escape') setFocused(false); }} />
            {focused && suggestions.length > 0 && <ul className="suggestions" aria-label="Autocomplete suggestions">
              {suggestions.map((term) => <li key={term}>
                <button type="button" onClick={() => select(term)}>{term}</button>
              </li>)}
            </ul>}
          </div>
          <button type="submit">{loading ? 'Searching…' : 'Search'}</button>
        </div>
        <div className="filters">
          {Object.entries(names).map(([key, label]) => <label key={key}>{label}
            <select aria-label={label} value={filters[key]} onChange={(event) => setFilters({ ...filters, [key]: event.target.value })}>
              <option value="">All</option>
              {Array.isArray(options[key]) && options[key].map((value) => <option key={value}>{value}</option>)}
            </select>
          </label>)}
          <label>Results per page
            <select aria-label="Results per page" value={k} onChange={(event) => setK(Number(event.target.value))}>
              {[5, 10, 20, 50].map((value) => <option key={value}>{value}</option>)}
            </select>
          </label>
          <button type="button" className="secondary" onClick={() => { setFilters(defaults); search(query, defaults); }}>Clear filters</button>
        </div>
      </form>
      {filterError && <p role="alert">{filterError}</p>}
    </section>
    {loading && <p role="status">Searching the index…</p>}
    {error && <p role="alert" className="error">{error}</p>}
    {data && <section aria-label="Search results">
      <p className="meta">{data.total} matching {data.total === 1 ? 'passage' : 'passages'}
        {Object.values(applied.values).filter(Boolean).length > 0 && ' · ' + Object.values(applied.values).filter(Boolean).join(' · ')}
      </p>
      {warning && <p role="status">Boolean syntax could not be parsed. Showing keyword results.</p>}
      {data.did_you_mean && <p>Did you mean <button className="secondary" onClick={() => {
        setQuery(data.did_you_mean); search(data.did_you_mean, applied.values);
      }}>{data.did_you_mean}</button>?</p>}
      {!data.results.length && <p className="panel">No matching passages found.</p>}
      <Results data={data} user={user} />
      {(data.page > 1 || data.page * data.k < Math.min(1000, data.total)) && <nav className="actions" aria-label="Result pages">
        <button className="secondary" disabled={data.page <= 1} onClick={() => search(applied.text, applied.values, data.page - 1, data.k)}>Previous</button>
        <span>Page {data.page}</span>
        <button className="secondary" disabled={data.page * data.k >= Math.min(1000, data.total)}
          onClick={() => search(applied.text, applied.values, data.page + 1, data.k)}>Next</button>
      </nav>}
    </section>}
  </>;
}
