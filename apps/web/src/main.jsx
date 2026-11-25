import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Account, Bookmarks } from './Account.jsx';
import { Dashboard } from './Dashboard.jsx';
import { Search } from './Search.jsx';
import { api } from './api.js';
import './style.css';

function AccessDenied() {
  return <section className="panel">
    <h1>Access denied</h1>
    <p role="alert">Your account does not have access to this page.</p>
    <a href="#search">Return to search</a>
  </section>;
}

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
    window.location.hash = profile.role === 'citizen' ? 'profile' : profile.role;
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
  } else if (['bookmarks', 'profile', 'dashboard', 'department', 'admin'].includes(route)) {
    if (loading) page = <p role="status">Checking your account…</p>;
    else if (!user) page = <p className="panel"><a href="#login">Sign in</a> to continue.</p>;
    else if (route === 'bookmarks') page = <Bookmarks />;
    else if (route === 'profile') page = <section className="panel">
      <h1>Your profile</h1>
      <p>Username <strong>{user.username}</strong></p>
      <p>Role <strong>{user.role}</strong></p>
    </section>;
    else if ((route === 'department' && user.role === 'department')
      || (route === 'admin' && user.role === 'admin') || (route === 'dashboard' && staff)) {
      page = route === 'dashboard' ? <Dashboard user={user} /> : <section className="panel">
        <h1>{route === 'admin' ? 'Administrator' : 'Department'} home</h1>
        <p>Signed in as <strong>{user.username}</strong>.</p>
        <a href="#dashboard">Open document dashboard</a>
      </section>;
    } else page = <AccessDenied />;
  } else if (route === 'unauthorized') {
    page = <AccessDenied />;
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
            <a href="#profile">Profile</a>
            {staff && <a href={`#${user.role}`}>Dashboard</a>}
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
