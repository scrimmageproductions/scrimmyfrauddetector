# Human Field Investigator Protocol

A Scrimmy dossier is a **lead**, not evidence of wrongdoing. Its score only says that public records disagree with each other in ways worth checking. Most leads will turn out to have ordinary explanations. This protocol takes a lead from desk to a decision. Only a human decides whether anything gets reported, and the tool never submits anything.

## 0. Ground rules

1. **Presume a lawful explanation** until you have ruled it out with primary records. Write down every explanation you considered.
2. **Collect lawfully.** Use public records, public right-of-way observation, and formal records requests. Never trespass, enter premises, use a pretext or false identity, access accounts, or contact the subject's employees, clients or families.
3. **Do not tip off or confront** the subject. Do not post about a lead or circulate it. Premature disclosure can be defamatory, can compromise a sealed qui tam case, and can lead to destroyed records.
4. **Never photograph or record children.** This matters most for child care leads. Photograph structures and signage only, framed to exclude people wherever possible.
5. **Stay safe.** If you are approached or challenged, leave. No lead is worth a confrontation.

## 1. Desk verification (always first)

Work through the dossier's Ground-Truthing Checklist. For each source, save a dated copy (PDF or screenshot with the URL and timestamp) in the lead's case folder.

| Check | Where | What you are looking for |
|---|---|---|
| Reproduce the screen | URLs in the dossier's Source Ledger | Same results? Record any differences. Re-hash responses and compare with `response_sha256`. |
| Corporate filings | State Secretary of State business search | Legal formation date, status, registered agent, officers/organizers, assumed names, annual reports, predecessor entities or mergers. |
| Federal registration | SAM.gov entity search; SAM.gov exclusions; HHS-OIG LEIE; state Medicaid exclusion lists | Entity start date, physical vs. mailing address, points of contact, exclusions or debarments. |
| Property records | County assessor / recorder / treasurer | Owner of record, property class code (residential, commercial), homestead exemption, sale history, whether the owner is connected to listed officers. |
| Zoning | Municipal zoning map; home-occupation permits | Whether the funded activity is allowed at the address. |
| Licensing | State child care license lookup, health facility licensing, professional license boards | License type (a *family* child care home legitimately operates in a residence), licensed capacity, inspection reports, violations. |
| Provider registries | NPPES (manual search on name variants and individual NPIs); state Medicaid provider directory | Registration under a different name, a parent organization, or individual practitioners. |
| Award files | USASpending award pages; agency award notices; Federal Audit Clearinghouse | Purpose, place of performance, sub-awards, and Single Audit findings (required at $1,000,000+ in annual federal expenditures for fiscal years beginning on or after Oct 1, 2024; $750,000 before that). |
| Records requests | FOIA (federal); state public-records law, e.g. the Minnesota Government Data Practices Act | Applications, site-visit reports and claims data from pass-through state agencies. |
| Imagery | Street View history; county aerial imagery | Whether a business presence existed **during the award period**, not just today. |
| Courts | PACER; state court portals | Prior civil judgments, related litigation, prior enforcement. |

After the desk work, classify each triggered indicator as:

- **Explained**: a primary record resolves it (for example, a licensed family child care home, a registered-agent office, or an affiliate under common ownership).
- **Unresolved**: still inconsistent after checking primary records.
- **Corroborated**: an independent primary record shows the same discrepancy.

If every indicator is *Explained*, close the lead, record why, and securely delete the personal data you collected.

## 2. Field observation (only if material discrepancies remain)

- Observe only from **public right-of-way** (sidewalk, street, public parking). Never enter the property, a lobby or a mailroom.
- Make **at least two visits on different days, at times the funded services should be running** (for example, a child care center's hours, or a clinic's posted hours).
- Record: date, time, weather, signage, posted hours, visible business activity, deliveries, and whether the structure matches the use (for example, a single-family house with no commercial signage, or a shipping storefront where the "suite" is a mailbox).
- Photos: keep the original files with EXIF time and GPS intact, write a hash of each file into your notes on the day, and never edit the originals. Leave out faces, license plates and children.
- Write contemporaneous notes the same day: what you saw, not what you think it means.

## 3. Assessment

Build a short matrix for each indicator showing the observation, the primary-source record, the lawful explanations considered, and the status (Explained, Unresolved or Corroborated). Then decide:

- **Close**: everything is explained.
- **Monitor**: some indicators are unresolved but immaterial. Re-check after the next award cycle.
- **Consider referral**: material, corroborated discrepancies tied to federal funds.

## 4. Before drafting any tip

1. **Talk to a whistleblower attorney first** if you are considering a False Claims Act (qui tam) action. Qui tam complaints are filed **under seal** by counsel (31 U.S.C. § 3730(b)). The **public disclosure bar** (§ 3730(e)(4)) can defeat a case built only on public databases such as USASpending unless you qualify as an *original source* with independent knowledge. First-to-file rules also apply.
2. **Choose the right official channel**: the awarding agency's Office of Inspector General hotline (directory at oversight.gov), HHS-OIG for Medicare/Medicaid matters, USDA-OIG for child nutrition programs, the state Medicaid Fraud Control Unit, or the federal whistleblower program you intend to use.
3. **Write facts, not conclusions.** Separate *observed* from *inferred*. Cite a primary record for every statement. Use neutral wording ("records show", "no license located", "observed on [date] at [time]"). Never write "X is committing fraud."
4. **Attach your verification records**, not just the dossier. The Scrimmy score explains why you looked. It is not evidence.
5. **Keep it confidential.** Do not share the tip, the dossier or your findings publicly or with the subject.

## 5. Records hygiene

- Keep each lead's folder encrypted and access-controlled. Log who accessed it and when.
- Keep the original dossier JSON unchanged. Put your annotations in separate files.
- When a lead is closed as explained, delete the personal data about individuals collected for it, and keep only a short closure note.
