# Weekly Company-Discovery Review

Generated: 2026-08-29T11:30:06.456241+00:00

Orchestrator: verify the decisions below, then teach the worker by
editing `discovery_rules.json` (blocklist_names, force_watch_names,
min_fresher_to_admit, notes). The worker reads that file every cycle.

## Activity (last 7 days)
- discovery cycles: 19
- candidates probed: 502
- ADDED to list: 3 -> Jitterbit, Jitterbit, Jitterbit
- PROMOTED from watchlist: 0 -> none
- watchlist size: 12

## Rejection reasons (nothing is deleted; all are re-checked)
- 467 x no public ATS endpoint found
- 17 x no India-based openings
- 15 x no fresher-eligible openings right now

## Near-misses to review (real India hiring, no fresher role yet)
These are the highest-risk calls: if the worker is wrong about a
company, it will most likely be one of these.
- GoKwik (keka) - 19 India jobs, 0 fresher, checked 1x
- Refyne (smartrecruiters) - 9 India jobs, 0 fresher, checked 1x
- Iris Software Inc. (smartrecruiters) - 4 India jobs, 0 fresher, checked 12x
- Dhan (keka) - 4 India jobs, 0 fresher, checked 1x
- Skyflow (ashby) - 4 India jobs, 0 fresher, checked 1x
- Capgemini (successfactors) - 1 India jobs, 0 fresher, checked 4x

## Long-parked watchlist entries (checked most often, still no fresher)
- Iris Software Inc. - checked 12x, best fresher seen 0
- Coalition Technologies - checked 5x, best fresher seen 0
- BOLD - checked 5x, best fresher seen 0
- Capgemini - checked 4x, best fresher seen 0
- Kellton Tech Solutions - checked 1x, best fresher seen 2
- InfoBeans Technologies - checked 1x, best fresher seen 0
- WorldQuant - checked 1x, best fresher seen 6
- Dhan - checked 1x, best fresher seen 0
- Navi Technologies - checked 1x, best fresher seen 1
- Refyne - checked 1x, best fresher seen 0

## Current taught rules
- blocklist: empty
- force_watch: ['InfoBeans Technologies', 'Iris Software Inc.', 'Kellton Tech Solutions']
- min_fresher_to_admit: 1
- notes: ['2026-08-23 (orchestrator): watchlist is the anti-forgetting mechanism - never delete a verified-real company just because it has no fresher opening today.', '2026-08-23 (orchestrator): quality over quantity. Prefer parking an uncertain company on the watchlist over admitting it.', "2026-08-28 (weekly review): verified all 6 of the worker's parked/added calls live - every one correct. Jitterbit admission justified (9 India, 2 fresher, scans OK).", '2026-08-28 (weekly review): force-watching Kellton Tech, Iris Software, InfoBeans - Indian/India-presence companies whose ATS boards currently tag no India roles. Kellton shows 2 fresher roles but none tagged India, so it is a likely near-term promotion.', "2026-08-28 (orchestrator): rejection stats are dominated by 'no public ATS endpoint found' (207 of 227). LLM-proposed names are mostly companies without public career APIs - prefer categories known to use Greenhouse/Lever/SmartRecruiters/Keka, and keep mining our own postings.", '2026-08-29: New ATS unlocked: successfactors (probe + extractor). Big-4/GCC employers host it on their own domains (careers.<co>.com or jobs.<co>.com with /search/?q= server-rendered pages). Confirmed live: EY GDS (51 fresher-titled India roles), SAP India. Ruled out (different systems/blocked): Deloitte USI=Avature, Cognizant=403, Bosch/Siemens/Wipro/HCLTech/TechM/LTIM/Genpact/DXC/Mphasis. When proposing candidates, consider consulting GCCs, German engineering GCCs, and enterprise software firms - many use SuccessFactors.']
