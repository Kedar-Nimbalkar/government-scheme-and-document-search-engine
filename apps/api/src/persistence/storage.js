import { createHash, randomUUID } from 'node:crypto';
import { link, lstat, mkdir, open, readFile, readdir, realpath, rename } from 'node:fs/promises';
import { dirname, isAbsolute, join, relative, resolve } from 'node:path';

const MAX_PDF_BYTES = 16 * 1024 * 1024;
const HASH_PDF = /^[0-9a-f]{64}\.pdf$/;
const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;

export function sha256(bytes) {
  return createHash('sha256').update(bytes).digest('hex');
}

export async function committedDocumentIds(indexRoot) {
  let contents;
  try {
    contents = await readFile(join(indexRoot, 'manifest.json'), 'utf8');
  } catch (error) {
    if (error.code === 'ENOENT') return [];
    throw error;
  }
  const manifest = JSON.parse(contents);
  const ids = manifest.committed_doc_ids;
  if (manifest.schema_version !== 2 || manifest.tokenizer_version !== 'nfkc-casefold-v1'
      || !Array.isArray(ids) || ids.some((id) => typeof id !== 'string' || !UUID.test(id))
      || new Set(ids).size !== ids.length) {
    throw new Error('Invalid or incompatible index inventory');
  }
  return ids;
}

async function controlledDirectory(pdfRoot, name, create = false) {
  const root = await realpath(pdfRoot);
  const directory = join(root, name);
  if (create) await mkdir(directory, { recursive: true });
  if (await realpath(directory) !== directory) throw new Error('Unsafe PDF storage directory');
  return directory;
}

export async function stagedPdf(pdfRoot, stagedName) {
  if (typeof stagedName !== 'string' || !/^[A-Za-z0-9_-]+\.pdf$/.test(stagedName)) {
    throw new Error('Invalid PDF staging name');
  }
  const directory = await controlledDirectory(pdfRoot, '.staging');
  const path = join(directory, stagedName);
  const stat = await lstat(path);
  if (!stat.isFile() || stat.size < 5 || stat.size > MAX_PDF_BYTES || await realpath(path) !== path) {
    throw new Error('Invalid staged PDF');
  }
  const handle = await open(path, 'r+');
  try {
    const contents = await handle.readFile();
    if (contents.length > MAX_PDF_BYTES || contents.subarray(0, 5).toString() !== '%PDF-') {
      throw new Error('Invalid staged PDF');
    }
    await handle.sync();
    return { path, hash: sha256(contents) };
  } finally {
    await handle.close();
  }
}

async function syncDirectory(directory) {
  if (process.platform === 'win32') return;
  const handle = await open(directory, 'r');
  try {
    await handle.sync();
  } finally {
    await handle.close();
  }
}

export async function promotePdf(pdfRoot, staged) {
  const directory = await controlledDirectory(pdfRoot, 'uploads', true);
  const filename = staged.hash + '.pdf';
  const path = join(directory, filename);
  const filePath = 'uploads/' + filename;
  try {
    // Link the synced original without overwriting an existing file.
    await link(staged.path, path);
  } catch (error) {
    if (error.code !== 'EEXIST') throw error;
    const existing = await storedFile(pdfRoot, filePath);
    if (!existing || sha256(await readFile(existing)) !== staged.hash) throw new Error('Stored PDF checksum mismatch');
  }
  await syncDirectory(directory);
  return filePath;
}

export async function storedFile(pdfRoot, filePath) {
  const absolute = resolve(pdfRoot, filePath);
  const name = relative(resolve(pdfRoot), absolute);
  if (name === '' || name === '..' || name.startsWith('..\\') || name.startsWith('../') || isAbsolute(name)) return null;
  try {
    const actual = await realpath(absolute);
    const expected = join(await realpath(pdfRoot), name);
    if (actual !== expected || !(await lstat(absolute)).isFile()) return null;
    return actual;
  } catch (error) {
    if (error.code === 'ENOENT' || error.code === 'ENOTDIR') return null;
    throw error;
  }
}

export async function managedPdfs(pdfRoot) {
  const paths = [];
  for (const name of ['uploads', 'imports']) {
    let directory;
    try {
      directory = await controlledDirectory(pdfRoot, name);
    } catch (error) {
      if (error.code === 'ENOENT') continue;
      throw error;
    }
    for (const item of await readdir(directory, { withFileTypes: true })) {
      if (item.isFile() && HASH_PDF.test(item.name)) paths.push(name + '/' + item.name);
    }
  }
  return paths.sort();
}

export async function quarantinePdf(pdfRoot, filePath) {
  const original = await storedFile(pdfRoot, filePath);
  if (!original) return false;
  const directory = await controlledDirectory(pdfRoot, '.quarantine', true);
  await rename(original, join(directory, randomUUID() + '-' + filePath.split('/').at(-1)));
  await syncDirectory(directory);
  await syncDirectory(dirname(original));
  return true;
}
