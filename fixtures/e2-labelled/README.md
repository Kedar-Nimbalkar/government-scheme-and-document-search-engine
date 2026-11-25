# Synthetic E2 labelled corpus

The [labels](corpus.json) describe 16 passages across 15 synthetic documents.
They exercise repeated terms, phrase boundaries, tie dates, UTF-16 highlighting,
several states, missing attributes, CSV page numbers, and a pending document.
The pytest fixture compiles them with E1's tokenizer and segment writer, then
loads the resulting v2 disk snapshot through E2. Fixture metadata stays in
memory. The separate standalone PostgreSQL test uses real E1 and E3 roles.
