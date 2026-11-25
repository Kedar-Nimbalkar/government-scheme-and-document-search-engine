import React, { useEffect, useState } from 'react';
import { api } from './api.js';

const failures = {
  PDF_NO_TEXT: 'PDF has no searchable text', PDF_LIMIT: 'PDF exceeds extraction limits',
  PDF_CORRUPT: 'PDF is corrupt', WORKER_TIMEOUT: 'Indexing worker timed out',
  WORKER_EXIT: 'Indexing worker stopped', LEASE_EXPIRED: 'Indexing lease expired',
  TASK_LIMIT: 'Indexing task exceeds limits', STAGE_LIMIT: 'Parsed document exceeds staging limits',
};

function DepartmentAccounts() {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api('/admin/users').then((response) => setData(response.data)).catch((failure) => setError(failure.message));
  }, []);

  async function provision(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const body = Object.fromEntries(new FormData(form));
    setError(''); setNotice(''); setBusy(true);
    try {
      await api('/admin/users', { method: 'POST', body });
      form.reset();
      setNotice('Department account created.');
      setData((await api('/admin/users')).data);
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  }

  return <section className="panel">
    <h2>Department accounts</h2>
    {error && <p className="error" role="alert">{error}</p>}
    {!data && !error && <p role="status">Loading accounts…</p>}
    {notice && <p role="status">{notice}</p>}
    {data && <>
      <form onSubmit={provision}>
        <div className="form-grid">
          <label>Username<input name="username" required maxLength={80} autoComplete="off" /></label>
          <label>Initial password<input name="password" type="password" required minLength={8} autoComplete="new-password" /></label>
          <label>Department<select name="dept_id" aria-label="Department" required defaultValue="">
            <option value="" disabled>Select a department</option>
            {data.departments.map((department) => <option value={department.dept_id} key={department.dept_id}>{department.dept_name}</option>)}
          </select></label>
        </div>
        <button disabled={busy || !data.departments.length}>{busy ? 'Creating…' : 'Create department account'}</button>
      </form>
      <div className="table-wrap"><table>
        <thead><tr><th>Username</th><th>Role</th><th>Department</th></tr></thead>
        <tbody>{data.users.map((user) => <tr key={user.user_id}><td>{user.username}</td><td>{user.role}</td>
          <td>{data.departments.find((row) => row.dept_id === user.dept_id)?.dept_name || '—'}</td></tr>)}</tbody>
      </table></div>
    </>}
  </section>;
}

export function Dashboard({ user }) {
  const [schemes, setSchemes] = useState(null);
  const [documents, setDocuments] = useState(null);
  const [uploadError, setUploadError] = useState('');
  const [listError, setListError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    let polling = false;
    const refresh = async () => {
      if (polling) return;
      polling = true;
      try {
        const { data } = await api('/documents', { signal: controller.signal });
        setDocuments(data); setListError('');
      } catch (error) {
        if (!controller.signal.aborted) setListError(error.message);
      } finally { polling = false; }
    };
    api('/schemes', { signal: controller.signal }).then(({ data }) => setSchemes(data))
      .catch((error) => { if (!controller.signal.aborted) setUploadError(error.message); });
    refresh();
    const interval = setInterval(refresh, 2500);
    return () => { controller.abort(); clearInterval(interval); };
  }, []);

  async function upload(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const body = new FormData(form);
    setBusy(true); setUploadError(''); setNotice('');
    try {
      const { data } = await api('/documents', { method: 'POST', body });
      setNotice(`Upload accepted. Job ${data.job_id} is ${data.status}.`);
      form.reset();
      setDocuments((await api('/documents')).data);
    } catch (error) { setUploadError(error.message); }
    finally { setBusy(false); }
  }

  return <>
    <h1>{user.role === 'admin' ? 'Administrator dashboard' : 'Department dashboard'}</h1>
    <section className="panel">
      <h2>Upload a PDF</h2>
      {uploadError && <p role="alert" className="error">{uploadError}</p>}
      {!schemes && !uploadError && <p role="status">Loading available schemes…</p>}
      {schemes?.length === 0 && <p>No schemes are available for this account.</p>}
      {notice && <p role="status">{notice}</p>}
      <form onSubmit={upload}>
        <div className="form-grid">
          <label>Scheme<select name="scheme_id" aria-label="Scheme" required defaultValue="">
            <option value="" disabled>Select a scheme</option>
            {schemes?.map((scheme) => <option value={scheme.scheme_id} key={scheme.scheme_id}>
              {scheme.scheme_name} · {scheme.department} · {scheme.state}
            </option>)}
          </select></label>
          <label>Document title<input name="title" required maxLength={250} /></label>
          <label>Circular number<input name="circular_no" required maxLength={100} /></label>
          <label>Issue date<input name="issue_date" type="date" required /></label>
          <label>PDF file<input name="file" type="file" accept=".pdf,application/pdf" required /></label>
        </div>
        <button disabled={busy || !schemes?.length}>{busy ? 'Validating and uploading…' : 'Upload PDF'}</button>
      </form>
    </section>
    <section className="panel">
      <h2>{user.role === 'admin' ? 'Documents across departments' : 'Your uploaded documents'}</h2>
      {listError && <p role="alert" className="error">{listError}</p>}
      {!documents && !listError && <p role="status">Loading indexing status…</p>}
      {documents?.length === 0 && <p>No uploaded documents.</p>}
      {documents?.length > 0 && <div className="table-wrap"><table>
        <thead><tr><th>Document</th><th>Scheme</th><th>Indexing status</th><th>Details</th></tr></thead>
        <tbody>{documents.map((document) => <tr key={document.doc_id}>
          <td>{document.title}</td><td>{document.scheme_name}</td>
          <td><span className="status">{document.index_status ?? 'Unavailable'}</span></td>
          <td>{document.failure_reason ? (failures[document.failure_reason] || document.failure_reason)
            : document.index_status === 'INDEXED' && document.source_type === 'PDF'
              ? <a href={'/api/documents/' + document.doc_id + '/pdf'} target="_blank" rel="noreferrer">Open PDF</a>
              : document.updated_at ? new Date(document.updated_at).toLocaleString() : '—'}</td>
        </tr>)}</tbody>
      </table></div>}
    </section>
    {user.role === 'admin' && <DepartmentAccounts />}
  </>;
}
