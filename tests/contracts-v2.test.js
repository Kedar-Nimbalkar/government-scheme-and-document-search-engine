const assert = require('node:assert/strict');
const { test } = require('node:test');
const { createHash } = require('node:crypto');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const Ajv = require('ajv/dist/2020').default;
const addFormats = require('ajv-formats');
const schema = require('../packages/contracts/v2/schemas.json');
const endpoints = require('../packages/contracts/v2/endpoints.json');
const fixtures = require('../fixtures/v2/responses.json');
const failures = require('../fixtures/v2/failures.json');
const manifest = require('../fixtures/v2/index/manifest.json');
const segment = require('../fixtures/v2/index/segment-0001.json');

const ajv = new Ajv({ allErrors: true });
addFormats(ajv);
ajv.addFormat('binary', () => true);
ajv.addSchema(schema, 'team06');
const validator = (name) => ajv.getSchema(`team06#/$defs/${name}`);

function check(name, value) {
  const validate = validator(name);
  assert.ok(validate(value), JSON.stringify(validate.errors));
}

test('all v2 endpoint schemas resolve and positive and error fixtures validate', () => {
  for (const endpoint of [...Object.values(endpoints.internal), ...Object.values(endpoints.public)]) {
    for (const name of [endpoint.request, endpoint.response, endpoint.query].filter(Boolean)) {
      assert.ok(validator(name), name);
    }
  }
  for (const [name, value] of Object.entries(fixtures)) check(name, value);
  check('publicSearchQuery', { q: 'housing', page: '1' });
  check('response', require('../fixtures/v2/empty-search.json'));
  for (const status of endpoints.error_statuses) check('error', failures[status]);
});

test('index fixture checksums and search identity agree', () => {
  check('manifest', manifest);
  check('segment', segment);
  const recorded = manifest.segments[0];
  const data = readFileSync(resolve(__dirname, '../fixtures/v2/index', recorded.file));
  assert.equal(createHash('sha256').update(data).digest('hex'), recorded.sha256);
  assert.deepEqual(segment.dictionary, Object.keys(segment.postings).sort());
  const [passage] = segment.passages;
  const [result] = fixtures.response.results;
  assert.equal(result.doc_id, passage.doc_id);
  assert.equal(result.doc_id, fixtures.accepted.doc_id);
  assert.equal(result.scheme_id, fixtures.document.scheme_id);
  assert.equal(result.passage_id, passage.passage_id);
  assert.equal(result.snippet, passage.text);
  for (const { start, end } of result.match_spans) {
    assert.ok(result.matched_terms.includes(result.snippet.slice(start, end).toLowerCase()));
  }
});

test('reject incompatible versions, unsafe identifiers, and private response fields', () => {
  assert.equal(validator('manifest')({ ...manifest, schema_version: 1 }), false);
  assert.equal(validator('segment')({ ...segment, tokenizer_version: 'other' }), false);
  assert.equal(validator('manifest')(require('../fixtures/v1/index/manifest.json')), false);
  assert.equal(validator('passageId')(2 ** 53), false);
  assert.equal(validator('passageId')('9007199254740991'), false);
  check('passageId', '9007199254740992');
  check('passageId', '10000000000000000');
  assert.equal(validator('user')({ ...fixtures.user, password_hash: 'private' }), false);
  assert.equal(validator('document')({ ...fixtures.document, file_path: 'private' }), false);
  assert.equal(validator('error')({ error: { code: 'INVALID_REQUEST', message: 'Bad input' } }), false);
});
