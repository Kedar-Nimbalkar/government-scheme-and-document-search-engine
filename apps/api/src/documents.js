import { randomUUID } from 'node:crypto';
import { mkdir, open, realpath, unlink } from 'node:fs/promises';
import { join } from 'node:path';
import multer from 'multer';
import { validatePdf } from './engines.js';
import { forbidden, id, invalid, object, string } from './http.js';

export async function uploadMiddleware(config) {
  const staging = join(config.pdfRoot, '.staging');
  await mkdir(staging, { recursive: true });
  if (await realpath(staging) !== join(await realpath(config.pdfRoot), '.staging')) {
    throw new Error('Unsafe PDF staging directory');
  }
  const parse = multer({
    storage: multer.diskStorage({
      destination: staging,
      filename: (_request, _file, callback) => callback(null, randomUUID() + '.pdf'),
    }),
    fileFilter: (_request, file, callback) => {
      if (file.mimetype !== 'application/pdf' || !/\.pdf$/i.test(file.originalname)) {
        return callback(invalid('Upload a PDF file'));
      }
      return callback(null, true);
    },
    limits: { fileSize: config.maxPdfBytes, files: 1, fields: 4, parts: 6, fieldSize: 4096 },
  }).single('file');
  return (request, response, next) => {
    if (!request.is('multipart/form-data')) return next(invalid('Use multipart form data to upload a PDF'));
    return parse(request, response, (error) => {
      if (error?.message === 'Unexpected end of form' || error?.message === 'Multipart: Boundary not found') {
        return next(invalid('Malformed multipart request'));
      }
      return next(error);
    });
  };
}

export async function queueUpload(repo, config, request, requestId) {
  const file = request.file;
  if (!file) throw invalid('Select a PDF file');
  try {
    const body = object(request.body, ['scheme_id', 'title', 'circular_no', 'issue_date']);
    const schemeId = id(body.scheme_id);
    const title = string(body.title, 'Title', 250);
    const circularNo = string(body.circular_no, 'Circular number', 100);
    const issueDate = string(body.issue_date, 'Issue date', 10);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(issueDate) || issueDate.startsWith('0000')
        || !Number.isFinite(Date.parse(issueDate)) || new Date(issueDate).toISOString().slice(0, 10) !== issueDate) {
      throw invalid('Use a valid issue date in YYYY-MM-DD format');
    }
    const userId = request.user.user_id;
    if (!await repo.canUpload(userId, schemeId)) {
      await repo.recordOperationalEvent('DEPARTMENT_SCOPE_DENIED', { userId, requestId });
      throw forbidden();
    }
    const handle = await open(file.path, 'r');
    try {
      const header = Buffer.alloc(5);
      await handle.read(header, 0, header.length, 0);
      if (header.toString() !== '%PDF-') throw invalid('File does not contain a PDF signature');
    } finally {
      await handle.close();
    }
    await validatePdf(config, file.path);
    const result = await repo.createDocumentAndJob({
      userId, schemeId, title, circularNo, issueDate, stagedName: file.filename, requestId,
    });
    return result;
  } catch (error) {
    if (error.status === 400) {
      await repo.recordOperationalEvent('UPLOAD_REJECTED', { userId: request.user.user_id, requestId }).catch(() => {});
    }
    throw error;
  } finally {
    await unlink(file.path).catch(() => {});
  }
}
