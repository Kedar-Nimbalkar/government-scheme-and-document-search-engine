import { integer, object, string } from './input.js';
import { invalid } from '../exceptions/api-error.js';

export function searchParameters(query) {
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

export function autocompletePrefix(query) {
  object(query, ['prefix']);
  return string(query.prefix, 'Prefix', 64);
}
