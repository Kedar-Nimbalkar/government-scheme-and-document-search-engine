# Team 06 · Sprint 2 integration handoff

Read the [original integration prompt](sprint-2-integration-prompt.md), this
brief, the [v2 contracts](../packages/contracts/v2/README.md) and the
[v2 fixtures](../fixtures/v2/) before taking an engine prompt. All four engines
must consume v2. The v1 artifacts remain available for historical comparison.

Read Gate 0, Gate 1 and the Sprint 1 Handbook for scope, requirements and
algorithms. The revised Sprint 2 and 3 plan was absent from this checkout.
The integration prompt supplies the stack and sprint allocation in this workspace.

| Owner | Sprint 2 assignment |
| --- | --- |
| E1 · Unnati Hiremath | FR-01–03. Build the Python multiprocessing index pipeline with one writer. Coordinate index format with E2 and intake with E3/E4. |
| E2 · Anvit Naik | FR-04–05 and baseline FR-06–07. Read v2 fixtures while E1/E3 proceed. Implement internal FastAPI search over the shared reader. |
| E3 · Khushi Devagiri | FR-08 and persistence for FR-11/13. Build PostgreSQL services around the base migration. Coordinate filter reads and upload acceptance. |
| E4 · Kedar Nimbalkar | FR-11–13. Use fixtures while E1/E3 proceed. Build public Express routes and React UI. |

Start E1/E3 foundations first. E2 and E4 can work against shared fixtures in
parallel. Keep engine commits separate and coordinate cross-engine edits.
Reserve full FR-09 and FR-10 for Sprint 3. The foundation provides the
supersession columns and an operational/security log table. Event emission
and full audit behavior are subsequent work.

Use React with Vite and JavaScript, Node LTS with Express as the only public
API, Python multiprocessing for E1, internal loopback FastAPI for E2,
PostgreSQL for E3, and local PDF and immutable index storage. Exclude OCR,
LLMs, Elasticsearch, Redis and paid services. Use the shared tokenizer and
index reader. Fixture evidence verifies interface agreement only. Report
real tests, commands, files and blockers in every engine handoff.

## PDF extractor decision

[PyMuPDF licensing](https://pymupdf.io/licensing) offers AGPLv3 or commercial
terms. No project licensing decision or commercial entitlement was supplied.
The foundation pins [pypdf 6.20.0](https://pypi.org/project/pypdf/6.20.0/) under
BSD-3-Clause. Its page-level `extract_text()` allows E1 to retain page numbers.
E1 and E4 must coordinate extractability validation with this choice.
