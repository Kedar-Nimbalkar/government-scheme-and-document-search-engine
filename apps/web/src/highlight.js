import { createElement, Fragment } from 'react';

export function Highlighted({ text, spans }) {
  const ranges = (spans || []).filter(({ start, end }) => Number.isInteger(start)
    && Number.isInteger(end) && start >= 0 && end > start && end <= text.length)
    .sort((a, b) => a.start - b.start || a.end - b.end);
  const parts = [];
  let cursor = 0;
  for (const { start, end } of ranges) {
    if (end <= cursor) continue;
    if (start > cursor) parts.push(text.slice(cursor, start));
    parts.push(createElement('mark', { key: end }, text.slice(Math.max(start, cursor), end)));
    cursor = end;
  }
  parts.push(text.slice(cursor));
  return createElement(Fragment, null, ...parts);
}
