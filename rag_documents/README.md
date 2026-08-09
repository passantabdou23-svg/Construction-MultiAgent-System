# Controlled RAG source documents

This folder contains source publications that have an explicit provenance record in
`manifest.json`. Do not add an anonymous PDF: every document must include its official
landing page, direct download URL, edition, jurisdiction, licence, expected page count,
and SHA-256 checksum.

## Current source

`UK_Approved_Document_A_Structure_2013.pdf` is the official GOV.UK publication
**Approved Document A: Structure** (ISBN 978 1 85946 508 0). The source page states that
GOV.UK content is available under the Open Government Licence v3.0 except where otherwise
stated.

The document provides statutory guidance for building work in England. It is included to
develop and evaluate page-level retrieval and citation. It must not be presented as an
Egyptian building code or as a substitute for review by a licensed engineer.

## Integrity rule

The ingestion command checks the PDF checksum, total page count, and expected searchable
page count against `manifest.json` before producing chunks. Page 52 of the current PDF is
an intentional online-version separator with no substantive text, so the controlled record
expects 53 text-bearing pages out of 54. If the publisher updates a source, add a new edition
record; do not silently replace the existing file.
