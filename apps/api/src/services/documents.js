import { open, unlink } from 'node:fs/promises';
import { validatePdf } from '../adapters/engines.js';
import { forbidden, invalid } from '../exceptions/api-error.js';
import { id, object, string } from '../validators/input.js';

export async function queueUpload(repo, config, { file, body: fields, userId, requestId }) {
  if (!file) throw invalid('Select a PDF file');
  try {
    const body = object(fields, ['scheme_id', 'title', 'circular_no', 'issue_date']);
    const schemeId = id(body.scheme_id);
    const title = string(body.title, 'Title', 250);
    const circularNo = string(body.circular_no, 'Circular number', 100);
    const issueDate = string(body.issue_date, 'Issue date', 10);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(issueDate) || issueDate.startsWith('0000')
        || !Number.isFinite(Date.parse(issueDate)) || new Date(issueDate).toISOString().slice(0, 10) !== issueDate) {
      throw invalid('Use a valid issue date in YYYY-MM-DD format');
    }
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
      await repo.recordOperationalEvent('UPLOAD_REJECTED', { userId, requestId }).catch(() => {});
    }
    throw error;
  } finally {
    await unlink(file.path).catch(() => {});
  }
}
