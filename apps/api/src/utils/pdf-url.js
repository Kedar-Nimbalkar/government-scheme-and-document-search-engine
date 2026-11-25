export const pdfUrl = (row) => ['PDF', 'SCHEME_PDF', 'OFFICIAL_PDF'].includes(row.source_type) ? `/api/documents/${row.doc_id}/pdf` : null;
