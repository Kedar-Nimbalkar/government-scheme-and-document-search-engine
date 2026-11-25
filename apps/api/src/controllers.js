import { credentials } from './auth.js';
import { queueUpload } from './documents.js';
import { ApiError, UUID, absent, id, integer, invalid, object, string, unavailable } from './http.js';

const pdfUrl = (row) => row.source_type === 'PDF' ? `/api/documents/${row.doc_id}/pdf` : null;

function searchParameters(query) {
  object(query, ['q', 'state', 'department', 'category', 'k', 'page']);
  const filters = {};
  for (const name of ['state', 'department', 'category']) {
    if (query[name] === undefined || (typeof query[name] === 'string' && !query[name].trim())) continue;
    filters[name] = string(query[name], name, 256);
  }
  const page = integer(query.page ?? 1, 1000);
  const k = integer(query.k ?? 10, 50);
  if (page * k > 1000) throw invalid('Page multiplied by result count must be at most 1000');
  return { query: string(query.q, 'Query', 512), filters, page, k };
}

export function controllers(repo, engine, auth, config) {
  return {
    register: async (request, response) => {
      const user = await auth.register(credentials(request.body, [], true));
      response.status(201).json({ user_id: user.user_id });
    },
    login: async (request, response) => {
      const result = await auth.login(credentials(request.body), response.locals.requestId);
      auth.setCookie(response, result.access_token);
      response.set('Cache-Control', 'no-store').json(result);
    },
    me: (request, response) => response.json(request.user),
    logout: async (request, response) => {
      await auth.logout(request, response);
      response.json({ status: 'logged_out' });
    },
    search: async (request, response) => {
      const { data, warning } = await engine.search(searchParameters(request.query));
      if (!Array.isArray(data?.results) || data.results.some((hit) => !hit || !UUID.test(hit.doc_id) || typeof hit.snippet !== 'string')) {
        throw unavailable('Invalid response from query service');
      }
      const metadata = await repo.searchMetadata([...new Set(data.results.map((hit) => hit.doc_id))]);
      const results = data.results.map((hit) => {
        const item = metadata.get(hit.doc_id);
        const ranges = Array.isArray(hit.match_spans) ? hit.match_spans.filter(({ start, end }) =>
          Number.isInteger(start) && Number.isInteger(end) && start >= 0 && end > start && end <= hit.snippet.length) : [];
        return { ...hit, match_spans: ranges, scheme_name: item?.scheme_name ?? null,
          state: item?.state ?? null, category: item?.category ?? null, pdf_url: pdfUrl(hit) };
      });
      if (warning === 'BOOLEAN_FALLBACK') response.set('X-Search-Warning', warning);
      response.json({ ...data, results });
    },
    autocomplete: async (request, response) => {
      object(request.query, ['prefix']);
      const { data } = await engine.autocomplete(string(request.query.prefix, 'Prefix', 64));
      response.json(data);
    },
    filters: async (_request, response) => response.json(await repo.listFilters()),
    bookmarks: async (request, response) => response.json(
      (await repo.listBookmarks(request.user.user_id)).map((row) => ({
        bookmark_id: row.bookmark_id, doc_id: row.doc_id, scheme_id: row.scheme_id, page_no: row.page_no,
        created_at: row.created_at, title: row.title, scheme_name: row.scheme_name, pdf_url: pdfUrl(row),
      })),
    ),
    bookmark: async (request, response) => {
      const body = object(request.body, ['doc_id', 'scheme_id', 'page_no']);
      const docId = body.doc_id === undefined ? null : id(body.doc_id);
      const schemeId = body.scheme_id === undefined ? null : id(body.scheme_id);
      const pageNo = body.page_no == null ? null : integer(body.page_no, 2147483647);
      if (Boolean(docId) === Boolean(schemeId) || (schemeId && pageNo !== null)) {
        throw invalid('Select one bookmark target. A page requires a document.');
      }
      response.status(201).json(await repo.createBookmark(request.user.user_id, { docId, schemeId, pageNo }));
    },
    removeBookmark: async (request, response) => {
      if (!await repo.deleteBookmark(request.user.user_id, integer(request.params.id, 2147483647))) throw absent();
      response.sendStatus(204);
    },
    document: async (request, response) => {
      const document = await repo.getDocument(id(request.params.id));
      if (!document) throw absent();
      response.json(document);
    },
    pdf: async (request, response, next) => {
      const file = await repo.getDocumentPdf(id(request.params.id));
      if (!file) throw absent();
      response.sendFile(file, { headers: { 'Content-Type': 'application/pdf', 'Content-Disposition': 'inline' } },
        (error) => { if (error) next(error); });
    },
    status: async (request, response) => {
      const result = await repo.getDocumentStatus(id(request.params.id),
        { userId: request.user?.user_id, requestId: response.locals.requestId });
      if (!result) throw absent();
      response.json(result);
    },
    upload: async (request, response) => {
      const result = await queueUpload(repo, config, request, response.locals.requestId);
      if (result.replayed) throw new ApiError(409, 'DUPLICATE', 'This PDF already has an ingestion job');
      response.status(202).json({ doc_id: result.doc_id, job_id: result.job_id, status: result.status });
    },
    documents: async (request, response) => response.json(await repo.listUploads(request.user.user_id)),
    schemes: async (request, response) => response.json(await repo.listUploadSchemes(request.user.user_id)),
    accounts: async (request, response) => response.json(await repo.listAccounts(request.user.user_id)),
    provision: async (request, response) => {
      const account = credentials(request.body, ['dept_id'], true);
      response.status(201).json(await auth.provision(request.user.user_id,
        { ...account, deptId: integer(request.body.dept_id, 2147483647) }));
    },
  };
}
