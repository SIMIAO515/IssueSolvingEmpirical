#!/usr/bin/env bash
set -eo pipefail

source "evaluation/utils/version_control.sh"

MODEL_CONFIG=$1
COMMIT_HASH=$2
AGENT=$3
EVAL_LIMIT=$4
MAX_ITER=$5
NUM_WORKERS=$6
DATASET=$7
SPLIT=$8
N_RUNS=$9

EXPERT_CHECK_INTERVAL=${10:-10}   
MAX_EXPERT_CHECKS=${11:-3}        

if [ -z "$NUM_WORKERS" ]; then
  NUM_WORKERS=1
  echo "Number of workers not specified, use default $NUM_WORKERS"
fi
checkout_eval_branch

if [ -z "$AGENT" ]; then
  echo "Agent not specified, use default CodeActAgent"
  AGENT="CodeActAgent"
fi

if [ -z "$MAX_ITER" ]; then
  echo "MAX_ITER not specified, use default 100"
  MAX_ITER=100
fi

if [ -z "$RUN_WITH_BROWSING" ]; then
  echo "RUN_WITH_BROWSING not specified, use default false"
  RUN_WITH_BROWSING=false
fi

if [ -z "$DATASET" ]; then
  echo "DATASET not specified, use default princeton-nlp/SWE-bench_Verified"
  DATASET="princeton-nlp/SWE-bench_Verified"
fi

if [ -z "$SPLIT" ]; then
  echo "SPLIT not specified, use default test"
  SPLIT="test"
fi

if [ -n "$EVAL_CONDENSER" ]; then
  echo "Using Condenser Config: $EVAL_CONDENSER"
else
  echo "No Condenser Config provided via EVAL_CONDENSER, use default (NoOpCondenser)."
fi

export EXPERT_CHECK_INTERVAL=${EXPERT_CHECK_INTERVAL}
export MAX_EXPERT_CHECKS=${MAX_EXPERT_CHECKS}
export ENABLE_EXPERT_REQUESTS=${ENABLE_EXPERT_REQUESTS:-true}
export RUN_WITH_BROWSING=$RUN_WITH_BROWSING
export SANDBOX_RUNTIME_CONTAINER_IMAGE="ghcr.io/all-hands-ai/runtime:oh_v0.51.1_oamvzks0hb7w6uxd_4ejxplfqihby2ddo"
echo "RUN_WITH_BROWSING: $RUN_WITH_BROWSING"
echo "EXPERT_CHECK_INTERVAL: $EXPERT_CHECK_INTERVAL"
echo "MAX_EXPERT_CHECKS: $MAX_EXPERT_CHECKS"
echo "ENABLE_EXPERT_REQUESTS: $ENABLE_EXPERT_REQUESTS"

get_openhands_version

echo "AGENT: $AGENT"
echo "OPENHANDS_VERSION: $OPENHANDS_VERSION"
echo "MODEL_CONFIG: $MODEL_CONFIG"
echo "DATASET: $DATASET"
echo "SPLIT: $SPLIT"
echo "MAX_ITER: $MAX_ITER"
echo "NUM_WORKERS: $NUM_WORKERS"
echo "COMMIT_HASH: $COMMIT_HASH"
echo "EVAL_CONDENSER: $EVAL_CONDENSER"

# Default to NOT use Hint
if [ -z "$USE_HINT_TEXT" ]; then
  export USE_HINT_TEXT=false
fi
echo "USE_HINT_TEXT: $USE_HINT_TEXT"
EVAL_NOTE="$OPENHANDS_VERSION"

# if not using Hint, add -no-hint to the eval note
if [ "$USE_HINT_TEXT" = false ]; then
  EVAL_NOTE="$EVAL_NOTE-no-hint"
fi

if [ "$RUN_WITH_BROWSING" = true ]; then
  EVAL_NOTE="$EVAL_NOTE-with-browsing"
fi

EVAL_NOTE="$EVAL_NOTE-expert-hybrid"
EVAL_NOTE="$EVAL_NOTE-check${EXPERT_CHECK_INTERVAL}"
EVAL_NOTE="$EVAL_NOTE-max${MAX_EXPERT_CHECKS}"

if [ "$ENABLE_EXPERT_REQUESTS" != "true" ]; then
  EVAL_NOTE="$EVAL_NOTE-norequest"
fi

if [ -n "$EXP_NAME" ]; then
  EVAL_NOTE="$EVAL_NOTE-$EXP_NAME"
fi

# Add condenser config to eval note if provided
if [ -n "$EVAL_CONDENSER" ]; then
  EVAL_NOTE="${EVAL_NOTE}-${EVAL_CONDENSER}"
fi

function run_eval() {
  local eval_note="${1}"
  COMMAND="poetry run python evaluation/benchmarks/swe_bench/run_infer_expert_hybrid.py \
    --agent-cls $AGENT \
    --llm-config $MODEL_CONFIG \
    --max-iterations $MAX_ITER \
    --eval-num-workers $NUM_WORKERS \
    --eval-note $eval_note \
    --dataset $DATASET \
    --split $SPLIT \
    --expert-check-interval $EXPERT_CHECK_INTERVAL \
    --max-expert-checks $MAX_EXPERT_CHECKS"

  if [ "$ENABLE_EXPERT_REQUESTS" != "true" ]; then
    COMMAND="$COMMAND --disable-expert-requests"
  fi

  if [ -n "$EVAL_LIMIT" ]; then
    echo "EVAL_LIMIT: $EVAL_LIMIT"
    COMMAND="$COMMAND --eval-n-limit $EVAL_LIMIT"
  fi

  # Run the command
  eval $COMMAND
}

unset SANDBOX_ENV_GITHUB_TOKEN # prevent the agent from using the github token to push

if [ -z "$N_RUNS" ]; then
  N_RUNS=1
  echo "N_RUNS not specified, use default $N_RUNS"
fi

# Skip runs if the run number is in the SKIP_RUNS list
SKIP_RUNS=(${SKIP_RUNS//,/ })
for i in $(seq 1 $N_RUNS); do
  if [[ " ${SKIP_RUNS[@]} " =~ " $i " ]]; then
    echo "Skipping run $i"
    continue
  fi
  current_eval_note="$EVAL_NOTE-run_$i"
  echo "EVAL_NOTE: $current_eval_note"
  run_eval $current_eval_note
done

checkout_original_branch