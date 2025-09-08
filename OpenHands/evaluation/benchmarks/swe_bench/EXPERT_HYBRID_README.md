# Expert-Hybrid: Dual-Agent Collaboration for SWE-Bench

## Overview

Expert-Hybrid extends the standard SWE-Bench evaluation by adding an expert agent that provides guidance to prevent common failure modes in automated software engineering tasks. This approach simulates real-world collaboration between junior and senior engineers.

## Key Features

- **Dual-agent architecture**: Execution agent + Expert agent
- **Two intervention modes**: Active consultation + Passive monitoring  
- **Failure mode prevention**: Detects and corrects tunnel vision, iteration ineffectiveness, and superficial fixes
- **Full SWE-Bench compatibility**: Drop-in replacement for standard evaluation

## Architecture

### Execution Agent
- Performs standard SWE-Bench workflow (explore → reproduce → implement → verify)
- Enhanced with mandatory expert consultation points
- Requests guidance when uncertain or at critical decision points

### Expert Agent  
- Senior engineering consultant role
- Analyzes execution progress and provides strategic guidance
- Prevents systematic failure patterns through targeted interventions

### Interaction Mechanisms

**Active Consultation**: Execution agent requests expert guidance using structured format:
```
I need expert consultation on [specific question]. Please provide professional guidance.
```

**Passive Monitoring**: Expert automatically reviews progress every N iterations (default: 25) and provides course correction if needed.

## Implementation

### Core Components

**EvaluationAwareAgent**: Wrapper that adds expert monitoring to any existing agent:
```python
class EvaluationAwareAgent:
    def step(self, state):
        if self.expert_judge.should_trigger_passive_check(current_iteration):
            return MessageAction(
                content=f"PASSIVE CHECK REQUEST: Iteration {current_iteration}",
                wait_for_response=True
            )
        return self.base_agent.step(state)
```

**ExpertJudge**: Handles expert decision making and response generation:
```python  
class ExpertJudge:
    def __init__(self, problem_statement, check_interval=10, max_checks=3):
        # Expert system with failure mode prevention prompts
        
    def handle_expert_request(self, agent_message, state):
        # Process active consultation requests
        
    def generate_passive_check_message(self, state, current_turn):
        # Generate automatic expert reviews
```

### Enhanced Prompting

The execution agent receives enhanced instructions that include:
- Mandatory consultation points (before exploration, before implementation)
- Structured request format for expert guidance  
- Collaborative workflow integration

The expert agent uses specialized prompts focused on:
- Systematic failure mode detection
- Strategic guidance and course correction
- Concrete verification steps and alternatives

## Usage

### Basic Usage
```bash
./evaluation/benchmarks/swe_bench/scripts/run_infer_expert_hybrid.sh \
  llm.deepseek HEAD CodeActAgent 50 40 1 princeton-nlp/SWE-bench_Verified test 1 25 10
```

### Parameters
- `llm.deepseek`: LLM configuration for execution agent
- `HEAD`: Git commit hash
- `CodeActAgent`: Agent class to use
- `50`: Number of instances to evaluate  
- `40`: Maximum iterations per instance
- `1`: Number of workers
- `princeton-nlp/SWE-bench_Verified`: Dataset
- `test`: Dataset split
- `1`: Number of runs
- `25`: Expert check interval (every 25 iterations)
- `10`: Maximum expert checks per instance

### Configuration Options

**Environment Variables**:
```bash
export EXPERT_CHECK_INTERVAL=25        # Passive check interval
export MAX_EXPERT_CHECKS=10           # Maximum passive checks per instance  
export ENABLE_EXPERT_REQUESTS=true   # Enable active consultation
```

**Command Line Arguments**:
```bash
python run_infer_expert_hybrid.py \
  --agent-cls CodeActAgent \
  --llm-config llm.deepseek \
  --max-iterations 40 \
  --eval-n-limit 50 \
  --expert-check-interval 25 \
  --max-expert-checks 10
```

## Expert Intervention Examples

### Passive Check Example
```
Turn 25: [Expert Review]: You've been doing extensive grep searches without finding the root cause. 
Instead of more keyword searches, create a minimal reproduction script to understand the actual 
execution flow. Focus on tracing the error path rather than pattern matching.

Please consider this guidance and continue working.
```

### Active Consultation Example  
```
Agent: I need expert consultation on my implementation approach. My proposed solution is to modify the 
runshell_db method to check for SSL-related parameters and add the corresponding command-line arguments 
to the psql command using --set=sslmode=value syntax. Is this the correct approach?

Expert: Your analysis is completely correct, but I need to correct a critical technical detail. 
PostgreSQL's psql client does NOT use --set syntax for SSL parameters. The correct approach is 
environment variables: PGSSLMODE, PGSSLROOTCERT, PGSSLCERT, PGSSLKEY. This follows the same pattern 
as the existing PGPASSWORD handling in the code.

You may proceed with confidence based on this expert assessment.
```

## Requirements

- OpenHands framework
- LLM configurations in `config.toml`:
  ```toml
  [llm.deepseek]          # For execution agent
  model="deepseek-chat"
  api_key="your_api_key"
  ```

Expert Agent configurations in `run_infer_expert_hybrid.py`:
 ```diff
455            response = litellm_completion(
456               model="openai/claude-3-5-sonnet-20241022",
457                    api_key="",
458                    base_url="",
459                     messages=[
460                   {'role': 'system', 'content': self.system_message},
461                    {'role': 'user', 'content': expert_query}
                ],
  ```


## Comparison with Standard SWE-Bench

| Feature | Standard SWE-Bench | Expert-Hybrid |
|---------|-------------------|---------------|
| Agent Architecture | Single agent | Dual agent (execution + expert) |  
| Failure Prevention | None | Systematic detection and intervention |
| Guidance Mechanism | None | Active consultation + passive monitoring |
| Success Rate | Baseline | Improved through expert guidance |

Expert-Hybrid maintains full compatibility with existing SWE-Bench infrastructure while adding collaborative intelligence to improve task completion rates and solution robustness.