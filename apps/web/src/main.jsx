import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Account, Bookmarks } from './Account.jsx';
import { Dashboard } from './Dashboard.jsx';
import { Search } from './Search.jsx';
import { api } from './api.js';
import './style.css';

function App() {
  const [location, setLocation] = useState(window.location.hash.slice(1));
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [route, params] = location.split('?', 2);
  const staff = user?.role === 'admin' || user?.role === 'department';

  useEffect(() => {
    const navigation = () => setLocation(window.location.hash.slice(1));
    const expire = () => setUser(null);
    window.addEventListener('hashchange', navigation);
    window.addEventListener('session-expired', expire);
    const controller = new AbortController();
    api('/auth/me', { signal: controller.signal }).then(({ data }) => setUser(data))
      .catch((failure) => { if (!controller.signal.aborted && failure.status !== 401) setError(failure.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => {
      controller.abort();
      window.removeEventListener('hashchange', navigation);
      window.removeEventListener('session-expired', expire);
    };
  }, []);

  function signedIn(profile) {
    setUser(profile); setError('');
    window.location.hash = profile.role === 'citizen' ? 'search' : 'dashboard';
  }
  async function signOut() {
    setError('');
    try { await api('/auth/logout', { method: 'POST' }); }
    catch (failure) {
      if (failure.status !== 401) { setError(failure.message); return; }
    }
    setUser(null);
    window.location.hash = 'search';
  }

  let page;
  if (route === 'login' || route === 'register') {
    page = <Account key={route} registration={route === 'register'} onSignedIn={signedIn} />;
  } else if (route === 'bookmarks' || route === 'dashboard') {
    if (loading) page = <p role="status">Checking your account…</p>;
    else if (!user) page = <p className="panel"><a href="#login">Sign in</a> to continue.</p>;
    else if (route === 'bookmarks') page = <Bookmarks />;
    else if (staff) page = <Dashboard user={user} />;
    else page = <p role="alert">A department or administrator account is required.</p>;
  } else {
    page = <Search key={location} user={user} initialQuery={new URLSearchParams(params).get('q') || ''} />;
  }
  return <>
    <header>
      <div className="header-inner">
        <a className="brand" href="#search">Government Scheme and Document Search Engine</a>
        <nav aria-label="Main navigation">
          <a href="#search">Search</a>
          {user ? <>
            <a href="#bookmarks">Bookmarks</a>
            {staff && <a href="#dashboard">Dashboard</a>}
            <span className="meta">{user.username}</span>
            <button className="secondary" onClick={signOut}>Sign out</button>
          </> : !loading && <><a href="#login">Sign in</a><a href="#register">Register</a></>}
        </nav>
      </div>
    </header>
    <main>
      {error && <p role="alert" className="error">{error}</p>}
      {page}
    </main>
  </>;
}

createRoot(document.getElementById('root')).render(<App />);
