import { UUID } from '../validators/input.js';
import { unavailable } from '../exceptions/api-error.js';
import { pdfUrl } from '../utils/pdf-url.js';

export function createSearchService(repo, engine) {
  return {
    async search(parameters) {
      const { data, warning } = await engine.search(parameters);
      if (!Array.isArray(data?.results) || data.results.some((hit) => !hit || !UUID.test(hit.doc_id) || typeof hit.snippet !== 'string')) {
        throw unavailable('Invalid response from query service');
      }
      const metadata = await repo.searchMetadata([...new Set(data.results.map((hit) => hit.doc_id))]);
      const results = data.results.map((hit) => {
        const item = metadata.get(hit.doc_id);
        const ranges = Array.isArray(hit.match_spans) ? hit.match_spans.filter((span) =>
          span && Number.isInteger(span.start) && Number.isInteger(span.end)
          && span.start >= 0 && span.end > span.start && span.end <= hit.snippet.length) : [];
        return { ...hit, match_spans: ranges, scheme_name: item?.scheme_name ?? null,
          state: item?.state ?? null, category: item?.category ?? null, pdf_url: pdfUrl(hit),
          regions: item?.regions ?? [], source_references: item?.source_references ?? [] };
      });
      return { data: { ...data, results }, warning };
    },
    async autocomplete(prefix) {
      const { data } = await engine.autocomplete(prefix);
      if (!Array.isArray(data?.suggestions) || data.suggestions.some((term) => typeof term !== 'string')) {
        throw unavailable('Invalid response from query service');
      }
      return data;
    },
    filters: () => repo.listFilters(),
    async health() {
      try {
        await Promise.all([engine.health(), repo.health()]);
      } catch {
        throw unavailable('A dependent service is unavailable');
      }
    },
  };
}
