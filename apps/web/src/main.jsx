import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { CONTRACT_VERSION, PUBLIC_ENDPOINTS } from '../../../packages/contracts/v2/http.js';

function App() {
  const [status, setStatus] = useState('Checking services…');
  useEffect(() => {
    const controller = new AbortController();
    fetch(PUBLIC_ENDPOINTS.health.path, { signal: controller.signal })
      .then(async (response) => {
        const health = await response.json();
        setStatus(response.ok && health.status === 'ok' && health.contract_version === CONTRACT_VERSION
          ? 'API and query service are reachable.'
          : 'A service is unavailable.');
      })
      .catch((error) => {
        if (error.name !== 'AbortError') setStatus('Could not reach the API.');
      });
    return () => controller.abort();
  }, []);

  return <><h1>Government Scheme and Document Search Engine</h1><p role="status">{status}</p></>;
}

createRoot(document.getElementById('root')).render(<App />);
