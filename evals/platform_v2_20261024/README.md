# Platform evaluation dataset, 2026-10-24

The new dataset contains 12 development cases across AAPL/MSFT/NVDA and 24 frozen cases across GOOGL/AMZN/META/ADBE/CRM/ORCL. Each company has direct revenue, net margin, revenue growth, and a missing-evidence control for FY2099. Companies are disjoint between splits; the original agent_backend_v1 dataset remains unchanged.

There are **27 supported-target candidates and 9 missing-evidence controls**. The original manifest's descriptive `scope` string mistakenly says 24/12; this is a metadata counting error. Its company lists, case files, source hashes, and preselected DeepSeek IDs remain intact. This note corrects the count without modifying the frozen manifest or case contents.

Source JSON is the public SEC Company Facts response downloaded before model evaluation. `manifest.json` records its exact URL and SHA-256. Model outputs and per-HTTP-attempt usage are stored separately under `runs/platform-v2-live/`. The evaluation tests numeric targets and explicit abstention, not the factual correctness of every sentence in generated prose.
