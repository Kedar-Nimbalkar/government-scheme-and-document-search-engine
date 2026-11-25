import { randomUUID } from 'node:crypto';
import { mkdir, realpath } from 'node:fs/promises';
import { join } from 'node:path';
import multer from 'multer';
import { invalid } from '../exceptions/api-error.js';

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
