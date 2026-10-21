# Field Investigator SOP

A Scrimmy dossier is a **lead**, not evidence of wrongdoing. Its score only says that public records disagree with each other in ways worth checking, and most leads will turn out to have ordinary explanations. This SOP takes a lead from desk review to a decision. Only a human decides whether anything is reported, and the tool never submits anything.

## 0. Ground rules

1. **Presume a lawful explanation** until primary records rule it out. Write down every explanation you considered.
2. **Collect lawfully.** Use public records, observation from public right-of-way, and formal records requests. Never trespass, enter premises, use a pretext or false identity, access accounts, or contact the subject's staff, clients or families.
3. **Never photograph, film, count or describe children or patients.** Photograph structures and signage only.
4. **Do not tip off, confront or publicize.** Premature disclosure can be defamatory, can break a qui tam seal, and can lead to destroyed records.
5. **Stay safe.** If you are approached or challenged, leave.
6. **Talk to counsel early** (see section 5). It protects you and the case.

## 1. Preserve before you investigate

1. Run `python -m scrimmy verify-evidence <evidence dir>` (or `sha256sum -c SHA256SUMS` inside it). Every file must report OK.
2. Copy the lead's evidence directory to write-once or access-controlled storage, and log who accessed it and when.
3. Re-run the dossier's Source Ledger URLs and save the new responses alongside the originals (never over them). Differences are themselves evidence: a registration that changes after award is a finding to note.

## 2. Desk verification

Work through the dossier's Ground-Truthing Checklist. For each source, save a dated copy (PDF or screenshot showing the URL and timestamp).

| Check | Where | What you are looking for |
|---|---|---|
| Corporate filings | State Secretary of State business search | Legal formation date, status, registered agent, officers/organizers, assumed names, annual reports, predecessor entities |
| Federal registration | SAM.gov entity search and exclusions | Entity start date, physical vs. mailing address, points of contact, debarments |
| Exclusions | HHS-OIG LEIE online search (supports SSN/EIN verification); state Medicaid exclusion lists | Confirms or rules out any LEIE match in the dossier. A name match alone is never enough. |
| County deed / assessor audit | County recorder, assessor and treasurer | Owner of record, property class code (residential vs. commercial), homestead exemption, transfer history, links between owner and officers |
| Zoning | Municipal zoning map; home-occupation and conditional-use permits | Whether the funded activity is permitted at the address |
| State licensing | Child care licensing lookup, health facility licensing, professional boards | License type (family/home vs. center), **licensed capacity**, ages served, hours, inspection reports, violations |
| Provider registries | NPPES (manual search on name variants and individual NPIs); state Medicaid provider directory | Registration under another name, a parent organization, or individual practitioners |
| Award files | USASpending award pages; agency award notices; Federal Audit Clearinghouse | Purpose, place of performance, sub-recipients, Single Audit findings |
| Imagery history | Street View history; county aerial imagery | Whether a business presence existed **during the award period** |
| Courts | PACER; state court portals | Prior judgments, related litigation, prior enforcement |

## 3. Operating capacity vs. billing claims

This is the most persuasive verification you can do, and it relies entirely on records.

1. **Pin down capacity from the license**: licensed capacity, ages served, days and hours of operation, and the license type. Check whether the operator runs other licensed sites that the same award funds; sponsors and multi-site operators are the most common lawful reason a ratio looks high.
2. **Replace the benchmark rate with the real one.** Look up the program's published rate for the award period: the USDA CACFP reimbursement-rate notice in the Federal Register, the state CCDF payment-rate schedule, or the CMS hospice per diem final rule. Recompute the dossier's arithmetic with it.
3. **Get the claimed volume, not just the dollars.** Use FOIA (federal) and state public-records requests to ask the pass-through state agency or sponsor for claimed meal counts, attendance or patient-days, site-monitoring reports and corrective-action plans. Agencies routinely release these, often with personal information redacted.
4. **Compute the maximum plausible claim**: capacity x operating days x reimbursable units per day (for example, meals per child per day). Compare it with the claimed volume, and record the inputs and their sources in a worksheet.
5. **Observe operation, not people.** On at least two visits during posted hours, record only whether the site appears to be operating: open or closed, signage, posted hours, building condition. Do not count or photograph attendees.

## 4. Assessment

Build a matrix for each indicator showing the observation, the primary record, the lawful explanations considered, and the status (Explained, Unresolved or Corroborated). Then decide:

- **Close**: all indicators are explained. Record why, then securely delete personal data collected for the lead.
- **Monitor**: unresolved but immaterial. Re-check after the next award cycle.
- **Consider referral**: material, corroborated discrepancies tied to federal funds. Go to section 5.

## 5. Preserving legal privilege and choosing a channel

1. **Retain whistleblower counsel before writing anything for submission.** Analysis and notes prepared at counsel's direction can qualify as attorney work product. Label such notes "Prepared at the direction of counsel," and keep them separate from your general notes.
2. **Do not share drafts** with journalists, advocacy groups, colleagues or online forums. Sharing can waive privilege, break qui tam confidentiality and create defamation exposure.
3. **If you work for, or near, the subject**, do not take documents, patient or child records, or protected health information without counsel's advice. Improper collection can be unlawful and can taint the case.
4. **Choose the channel with counsel.** Dossiers suggest a routing, but the decision belongs to you and your attorney:
   - **Track A, criminal or administrative referral**: the awarding agency's Office of Inspector General hotline (directory at oversight.gov), HHS-OIG, USDA-OIG for child nutrition programs, or the state Medicaid Fraud Control Unit. DOJ whistleblower programs have their own eligibility rules and award terms; read the current terms on justice.gov rather than relying on summaries.
   - **Track B, civil False Claims Act (qui tam)**: counsel files the complaint **under seal** (31 U.S.C. § 3730(b)) and serves the government with a disclosure statement of material evidence. Relator shares are set by § 3730(d). The **public disclosure bar** (§ 3730(e)(4)) can defeat cases built only on public databases unless you are an *original source* whose independent knowledge materially adds to the public information. Your field verification may help with that; your counsel decides. First-to-file rules also apply.
5. **Write facts, not conclusions.** Cite a primary record for every statement, keep observed facts separate from inferences, use neutral wording, and never write "X is committing fraud." The Scrimmy score explains why you looked; it is not evidence.
6. **Keep it confidential** until counsel or the receiving agency tells you otherwise.

## 6. Records hygiene

- Keep the original dossier JSON and evidence directory unchanged; put annotations in separate files.
- Store lead folders encrypted and access-controlled, with an access log.
- When a lead closes as explained, delete personal data about individuals and keep only a short closure note.
