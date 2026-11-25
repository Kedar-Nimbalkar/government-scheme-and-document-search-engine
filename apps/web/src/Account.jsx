import React, { useEffect, useState } from 'react';
import { api, pdfPage } from './api.js';

export function Account({ registration, onSignedIn }) {
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function submit(event) {
    event.preventDefault();
    const credentials = Object.fromEntries(new FormData(event.currentTarget));
    setBusy(true); setError('');
    try {
      if (registration) await api('/auth/register', { method: 'POST', body: credentials });
      await api('/auth/login', { method: 'POST', body: credentials });
      const { data } = await api('/auth/me');
      onSignedIn(data);
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  }
  return <section className="panel narrow">
    <h1>{registration ? 'Create citizen account' : 'Sign in'}</h1>
    <form onSubmit={submit}>
      <label>Username<input name="username" autoComplete="username" required maxLength={80} /></label>
      <label>Password<input name="password" type="password" required minLength={registration ? 8 : 1}
        autoComplete={registration ? 'new-password' : 'current-password'} /></label>
      {error && <p role="alert" className="error">{error}</p>}
      <button disabled={busy}>{busy ? 'Please wait…' : registration ? 'Create account' : 'Sign in'}</button>
    </form>
    <p><a href={registration ? '#login' : '#register'}>{registration ? 'Already have an account? Sign in' : 'Register as a citizen'}</a></p>
  </section>;
}

export function Bookmarks() {
  const [items, setItems] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    api('/bookmarks', { signal: controller.signal }).then(({ data }) => setItems(data))
      .catch((failure) => { if (!controller.signal.aborted) setError(failure.message); });
    return () => controller.abort();
  }, []);
  async function remove(item) {
    setError('');
    try {
      await api('/bookmarks/' + item.bookmark_id, { method: 'DELETE' });
      setItems((list) => list.filter((row) => row.bookmark_id !== item.bookmark_id));
    } catch (failure) { setError(failure.message); }
  }
  return <section>
    <h1>Saved bookmarks</h1>
    {error && <p role="alert" className="error">{error}</p>}
    {!items && !error && <p role="status">Loading bookmarks…</p>}
    {items?.length === 0 && <p>No bookmarks saved.</p>}
    {items?.map((item) => <article className="panel" key={item.bookmark_id}>
      <h2>{item.title || item.scheme_name || 'Title unavailable'}</h2>
      <p className="meta">{item.doc_id ? 'Document' : 'Scheme'}{item.page_no ? ' · Page ' + item.page_no : ''}</p>
      <div className="actions">
        {item.pdf_url && <a className="button secondary" href={pdfPage(item.pdf_url, item.page_no)} target="_blank" rel="noreferrer">Open PDF</a>}
        {item.scheme_name && <a href={'#search?' + new URLSearchParams({ q: item.scheme_name })}>Search this scheme</a>}
        <button className="secondary" onClick={() => remove(item)}>Remove</button>
      </div>
    </article>)}
  </section>;
}
