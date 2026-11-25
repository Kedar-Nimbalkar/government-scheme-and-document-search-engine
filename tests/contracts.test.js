const assert = require('node:assert/strict');
const { test } = require('node:test');
const search = require('../fixtures/v1/search.json');
const metadata = require('../fixtures/v1/metadata.json');
const ingestion = require('../fixtures/v1/ingestion.json');
const segment = require('../fixtures/v1/index/segment-0001.json');

test('v1 fixtures agree on document and page identifiers', () => {
  const [hit] = search.internal_response.results;
  const [passage] = segment.passages;
  assert.equal(hit.doc_id, passage.doc_id);
  assert.equal(hit.page_no, passage.page_no);
  assert.equal(hit.doc_id, metadata.document[0].doc_id);
  assert.equal(hit.doc_id, ingestion.job.doc_id);
  assert.equal(hit.doc_id, ingestion.accepted.doc_id);
  assert.equal(hit.snippet, search.public_response.results[0].snippet.replace(/<\/?mark>/g, ''));
});
