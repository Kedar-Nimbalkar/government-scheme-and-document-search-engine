import { credentials } from '../validators/auth.js';
import { queueUpload } from '../services/documents.js';
import { ApiError, absent, invalid } from '../exceptions/api-error.js';
import { id, integer, object } from '../validators/input.js';
import { pdfUrl } from '../utils/pdf-url.js';

export function resourceControllers(repo, auth, config) {
  return {
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
      const result = await queueUpload(repo, config, { file: request.file, body: request.body,
        userId: request.user.user_id, requestId: response.locals.requestId });
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
