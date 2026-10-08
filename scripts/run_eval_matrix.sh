#!/usr/bin/env bash
# Eval matrix: baseline vs verify->revise (small, large) and cascade vs fixed-large.
#
# Requires an LLM endpoint configured via env vars (see .env.example / docs/llm_access.md):
#   FINRECEIPTS_LLM_PROVIDER=anthropic|openai  FINRECEIPTS_LLM_BASE_URL=...
#   FINRECEIPTS_LLM_API_KEY_ENV=<NAME of the env var holding the key>  (the key is never passed here)
#   SMALL=<small-tier model>  LARGE=<large-tier model>
# Optional: SAMPLE=<n> SEED=<s> (seeded random subset; default: all items), IDS_FILE=<ids>, OUT=<dir>,
#   RUNS="small_baseline small_verify ..." (subset of runs), PRICES=<prices.json>.
# Calls are sequential and paced (FINRECEIPTS_MIN_CALL_INTERVAL_S, default 1s). A fatal API
# error (401/402/403/429 or an account-status message) makes `finreceipts eval` exit 3 and
# this script stops immediately.
#
# LLM-call upper bounds for N items: baseline N, verify <= 3N (max_revisions=2),
# cascade <= 2N (max_revisions=0).
set -uo pipefail
: "${SMALL:?set SMALL}" "${LARGE:?set LARGE}"
SEED="${SEED:-42}"
OUT="${OUT:-results/$(date +%Y%m%d)}"
RUNS="${RUNS:-small_baseline small_verify small_verify_receipts large_baseline large_verify cascade}"
COMMON=(--seed "$SEED")
[[ -n "${SAMPLE:-}" ]] && COMMON+=(--sample "$SAMPLE")
[[ -n "${PRICES:-}" ]] && COMMON+=(--prices "$PRICES")
# IDS_FILE: one item id per line (e.g. the complement of an earlier sample)
[[ -n "${IDS_FILE:-}" ]] && mapfile -t _IDS < "$IDS_FILE" && COMMON+=(--ids "${_IDS[@]}")
declare -A ARGS=(
  [small_baseline]="--model $SMALL --mode baseline"
  [small_verify]="--model $SMALL --mode verify --max-revisions 2"
  [small_verify_receipts]="--model $SMALL --mode verify --feedback receipts --max-revisions 2"
  [large_baseline]="--model $LARGE --mode baseline"
  [large_verify]="--model $LARGE --mode verify --max-revisions 2"
  [cascade]="--model $SMALL --mode cascade --escalate-model $LARGE --max-revisions 0"
)
mkdir -p "$OUT"
for name in $RUNS; do
  echo "=== $name ($(date '+%H:%M:%S'))"
  # shellcheck disable=SC2086
  finreceipts eval ${ARGS[$name]} "${COMMON[@]}" --out-dir "$OUT/$name" > "$OUT/$name.log" 2>&1
  rc=$?
  tail -n 3 "$OUT/$name.log" | grep -E "ABORTED" && true
  if [[ $rc -ne 0 ]]; then
    echo "!!! $name exited with $rc -- stopping matrix"; tail -n 5 "$OUT/$name.log"; exit $rc
  fi
done
python scripts/summarize_results.py "$OUT" > "$OUT/table.md"
cat "$OUT/table.md"
