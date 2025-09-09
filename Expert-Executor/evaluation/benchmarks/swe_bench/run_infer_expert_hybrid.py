#!/usr/bin/env python3
import asyncio
import json
import os
import glob
from typing import Dict, List, Optional, Any

import pandas as pd
from datasets import load_dataset
from litellm import completion as litellm_completion

import openhands.agenthub
from evaluation.benchmarks.swe_bench.run_infer import (
    AgentFinishedCritic,
    complete_runtime,
    filter_dataset,
    get_config,
    initialize_runtime,
    set_dataset_type,
)
from evaluation.benchmarks.swe_bench.run_infer import (
    get_instruction as base_get_instruction,
)
from evaluation.utils.shared import (
    EvalException,
    EvalMetadata,
    EvalOutput,
    make_metadata,
    prepare_dataset,
    reset_logger_for_multiprocessing,
    run_evaluation,
)
from openhands.controller.state.state import State
from openhands.core.config import (
    get_llm_config_arg,
    get_parser,
)
from openhands.core.config.condenser_config import NoOpCondenserConfig
from openhands.core.config.utils import get_condenser_config_arg
from openhands.core.logger import openhands_logger as logger
from openhands.core.main import create_runtime, run_controller, create_controller
from openhands.events.action import MessageAction
from openhands.events.serialization.event import event_from_dict, event_to_dict
from openhands.utils.async_utils import call_async_from_sync
from openhands.controller.agent_controller import AgentController
from openhands.events import EventSource


class EvaluationAwareAgent:
  
    
    def __init__(self, base_agent, expert_judge):
        self.base_agent = base_agent
        self.expert_judge = expert_judge
        
    def step(self, state):
       
        current_iteration = self.expert_judge.get_current_iteration(state)
      
        if self.expert_judge.should_trigger_passive_check(current_iteration):
            logger.info(f"[PASSIVE_CHECK] Agent requesting pause for expert check at iteration {current_iteration}")
     
            self.expert_judge.processed_passive_iterations.add(current_iteration)
     
            return MessageAction(
                content=f"🤖 **PASSIVE CHECK REQUEST**: Iteration {current_iteration} - Requesting automatic expert review to evaluate progress and provide guidance.",
                wait_for_response=True 
            )

        return self.base_agent.step(state)
    
    def __getattr__(self, name):

        return getattr(self.base_agent, name)


USE_HINT_TEXT = os.environ.get('USE_HINT_TEXT', 'false').lower() == 'true'
USE_INSTANCE_IMAGE = os.environ.get('USE_INSTANCE_IMAGE', 'false').lower() == 'true'
RUN_WITH_BROWSING = os.environ.get('RUN_WITH_BROWSING', 'false').lower() == 'false'


async def run_controller_with_expert_hooks(
    config,
    initial_user_action,
    runtime,
    expert_judge,  # ExpertJudge type - removed type hint to avoid forward reference issue
    fake_user_response_fn=None,
):

    from openhands.core.main import (
        create_agent, 
        create_memory,
        run_agent_until_done,
        read_input,
    )
    from openhands.events.action import NullAction, Action, MessageAction
    from openhands.events import Event, EventSource, EventStreamSubscriber
    from openhands.events.observation import AgentStateChangedObservation
    from openhands.core.schema import AgentState
    
    sid = runtime.event_stream.sid
    base_agent = create_agent(config)

    agent = EvaluationAwareAgent(base_agent, expert_judge)
    event_stream = runtime.event_stream
    memory = create_memory(
        runtime=runtime,
        event_stream=event_stream,
        sid=sid,
        selected_repository=config.sandbox.selected_repo,
        repo_directory=None,
        conversation_instructions=None,
        working_dir=config.workspace_mount_path_in_sandbox,
    )
    

    controller, initial_state = create_controller(
        agent, runtime, config, replay_events=None
    )
    

    expert_judge.controller = controller
    logger.info(f"[EXPERT_DEBUG] Setting controller: {controller}")
    logger.info(f"[EXPERT_DEBUG] Controller set verification: {expert_judge.controller}")
    logger.info(f"[EXPERT_DEBUG] Controller is same object: {expert_judge.controller is controller}")
    
    logger.info(f"Expert evaluation system initialized, check interval: {expert_judge.check_interval}")
    
    if isinstance(initial_user_action, Action):
        event_stream.add_event(initial_user_action, EventSource.USER)

    def on_event(event: Event) -> None:
        if isinstance(event, AgentStateChangedObservation):
            if event.agent_state == AgentState.AWAITING_USER_INPUT:
                logger.info(f"[EXPERT_DEBUG] AWAITING_USER_INPUT triggered, fake_user_response_fn is None: {fake_user_response_fn is None}")
        
                current_state = controller.get_state()
                last_agent_message = current_state.get_last_agent_message()
                logger.info(f"[EXPERT_DEBUG] Last agent message: {last_agent_message.content if last_agent_message else 'None'}")
                if fake_user_response_fn is None:
                    message = read_input(config.cli_multiline_input)
                else:
                    logger.info(f"[EXPERT_DEBUG] Calling fake_user_response_fn")
                    message = fake_user_response_fn(controller.get_state())
                    logger.info(f"[EXPERT_DEBUG] Got message from fake_user_response_fn: {message}")
                action = MessageAction(content=message)
                event_stream.add_event(action, EventSource.USER)

    event_stream.subscribe(EventStreamSubscriber.MAIN, on_event, sid)

    end_states = [
        AgentState.FINISHED,
        AgentState.REJECTED,
        AgentState.ERROR,
        AgentState.PAUSED,
        AgentState.STOPPED,
    ]

    try:
        await run_agent_until_done(controller, runtime, memory, end_states)
    except Exception as e:
        logger.error(f'Exception in main loop: {e}')

    # Save session if configured
    if config.file_store is not None and config.file_store != 'memory':
        end_state = controller.get_state()
        end_state.save_to_session(
            event_stream.sid, event_stream.file_store, event_stream.user_id
        )

    await controller.close(set_stop_state=False)
    state = controller.get_state()

    # Save trajectories if applicable
    if config.save_trajectory_path is not None:
        import os
        # if save_trajectory_path is a folder, use session id as file name
        if os.path.isdir(config.save_trajectory_path):
            file_path = os.path.join(config.save_trajectory_path, sid + '.json')
        else:
            file_path = config.save_trajectory_path
        
        with open(file_path, 'w') as f:
            json.dump(
                [event_to_dict(event) for event in event_stream.search_events(start_id=0)],
                f,
                indent=2,
            )
    
    return state



class ExpertJudge:
    
    def __init__(self, problem_statement: str, check_interval=10, max_checks=3):
        self.problem_statement = problem_statement
        self.check_interval = check_interval
        self.max_checks = max_checks
        self.active_request_count = 0
        self.passive_check_count = 0
        self.processed_passive_iterations = set()  
        self.fake_response_call_count = 0  
        self.controller = None  
        self.log_completions_folder = None 

        self.system_message = f"""
You are a senior software engineering expert specializing in breaking systematic failure patterns in AI-driven debugging. 
Your role is to provide professional judgment and corrective guidance to prevent the major failure modes that plague automated software repair.

**Original Problem**: {problem_statement}

**CRITICAL MISSION**: 
You must act as a "Second Opinion" expert to break cognitive tunnels and strategy rigidity that lead to systematic failures, 
and you must provide definite, actionable recommendations for implementation. 
Do NOT produce tentative or questioning outputs such as "Would you like me to...".


**PRIMARY FAILURE MODES TO PREVENT**:

1. **TUNNEL VISION & EARLY POSITIONING ERRORS**
   - Symptom: Agent blindly trusts issue description or focuses on keyword matches.
   - Risk: Fix may address symptoms but miss the true root cause.
   - Intervention: 
     * DO provide a concrete verification step (e.g., "Run `grep \"keyword\" file.py` to confirm where this is actually used.")
     * DO challenge initial assumptions with alternative explanations.
     * DO encourage impact domain analysis: ask "Where else does this pattern apply?"
     * DO check whether related or dependent components also need fixes (e.g., other modules, functions).

2. **INEFFECTIVE ITERATION & STRATEGY RIGIDITY**
   - Symptom: Agent repeats similar edits without progress or switches approaches chaotically.
   - Risk: Time wasted, loss of context, no substantive advancement.
   - Intervention:
     * DO recognize no-progress loops and suggest a reset.
     * DO propose a simpler new angle ("Instead of modifying logic X, try inspecting the input upstream with a debug print.")
     * DO remind agent of earlier discoveries that may have been forgotten.

3. **FUNDAMENTAL REPAIR STRATEGY FLAWS**
   - Symptom: Fix hides symptoms, overfits to single test, ignores framework conventions or Insufficient consideration of boundary conditions.
   - Risk: Superficial success, long-term fragility, or broken architecture.
   - Intervention:
     * DO validate the true root cause instead of masking errors.
     * DO enforce minimal, generalizable fixes instead of large risky refactors.
     * DO check compliance with framework/domain conventions.

4  **ISSUE SUGGESTIONS JUDGEMENT**
    If an issue offers suggestions, consider their rationality and choose the most reasonable way to implement them or challenge them if they seem flawed.

**EXPERT INTERVENTION STYLE**:
- IF the agent is in tunnel vision: DO suggest a specific quick check instead of vague "think broader".
- IF the agent proposes a complex fix: DO challenge feasibility and redirect to a simpler, safer modification.
- IF the agent is stuck in iteration: DO break the loop and reset strategy with a different perspective.

Note: You can suggest the agent to submit the issue as resolved, but only if you are confident that the fix is correct and complete.
"""

        
        self.chat_history = [{'role': 'system', 'content': self.system_message}]
        self.llm_config = get_llm_config_arg('llm.expert_judge')
    
    def extract_recent_history(self, state: State) -> List[Dict]:
        events = []
        history = getattr(state, 'history', [])
        
        for event in history: 
            try:
                if hasattr(event, '__dict__'):
                    event_dict = event_to_dict(event)
                else:
                    event_dict = event

                source = event_dict.get('source', '')
                if source in ['agent', 'user'] or 'observation' in event_dict.get('action', '').lower():
  
                    content = event_dict.get('content', '')
                    if not content and 'outputs' in event_dict:

                        outputs = event_dict.get('outputs', [])
                        if outputs:
                            content = str(outputs[0]) if len(outputs) == 1 else str(outputs)
                    
                    events.append({
                        'content': content,
                        'source': source,
                        'action': event_dict.get('action', ''),
                        'timestamp': event_dict.get('timestamp', ''),
                        'event_type': event_dict.get('action', 'unknown')
                    })
            except Exception as e:
                logger.warning(f"Error processing event: {e}")
                
        return events
    
    def get_real_time_context_from_state(self, state: State) -> str:
        history = state.history

        messages = []

        from openhands.events.action import Action
        from openhands.events.observation import Observation
        from openhands.events.action.message import SystemMessageAction, MessageAction
        
        for event in history:
            try:
                if isinstance(event, Action):

                    event_source = getattr(event, 'source', None)
                    content = getattr(event, 'content', str(event))
                    
                    if isinstance(event, SystemMessageAction):
                        messages.append({
                            'role': 'system',
                            'content': content
                        })
                    elif isinstance(event, MessageAction):
                        if event_source == 'user':
                            messages.append({
                                'role': 'user',
                                'content': content
                            })
                        elif event_source == 'agent':
                            messages.append({
                                'role': 'assistant',
                                'content': content
                            })
                    else:
          
                        if event_source == 'agent':
               
                            messages.append({
                                'role': 'assistant',
                                'content': content
                            })
                        elif event_source == 'user':
                            messages.append({
                                'role': 'user', 
                                'content': content
                            })
                            
                elif isinstance(event, Observation):
       
                    content = ''
                    if hasattr(event, 'content'):
                        content = getattr(event, 'content', '')
                    elif hasattr(event, 'outputs'):
                        outputs = getattr(event, 'outputs', [])
                        if outputs:
                            content = str(outputs[0]) if len(outputs) == 1 else str(outputs)
                    
   
                    if content:
                        messages.append({
                            'role': 'tool',
                            'content': content
                        })
                else:
          
                    content = getattr(event, 'content', str(event))
                    if content:
                        messages.append({
                            'role': 'system',
                            'content': content
                        })
                        
            except Exception as e:
       
                logger.warning(f"Failed to process event {type(event)}: {e}")
                continue

        all_messages = messages[1:] if len(messages) > 1 else messages
        

        interactions = []
        for i, msg in enumerate(all_messages):
            role = msg['role']
            content = msg['content']
       
            if role == 'user' and 'Expert Review' not in content:
                interactions.append(f"User Input: {content}")
            elif role == 'assistant':
                interactions.append(f"Agent Action: {content}")
            elif role == 'tool':
                interactions.append(f"Tool Result: {content}")
            elif role == 'system' and content:
                interactions.append(f"System: {content}")
        

        return "\n".join(interactions) if interactions else "No interactions found in history"
    
    def should_trigger_passive_check(self, current_iteration: int) -> bool:

        if self.passive_check_count >= self.max_checks:
            return False

        if current_iteration % self.check_interval != 0:
            return False

        if current_iteration in self.processed_passive_iterations:
            return False
            
        return True
    
    def get_current_iteration(self, state: State) -> int:

        iteration_flag = getattr(state, 'iteration_flag', None)
        if iteration_flag is not None:
            return iteration_flag.current_value
        else:

            return len([e for e in state.history if getattr(e, 'source', '') == 'agent'])
    
    def generate_passive_check_message(self, state: State, current_turn: int) -> Optional[str]:

        interactions_text = self.get_real_time_context_from_state(state)

        expert_query = f"""
**PASSIVE EXPERT REVIEW #{self.passive_check_count + 1} at Turn {current_turn}**
**Original Issue**: {self.problem_statement}
**Complete Recent Agent Activity:**
{interactions_text}

**SYSTEMATIC FAILURE MODE ASSESSMENT:**
As an expert reviewer, analyze the agent's current state through the lens of the three major failure patterns:

**TUNNEL VISION & POSITIONING ERRORS CHECK:**
- **Issue Misleading Risk**: If an issue offers suggestions, consider their rationality and choose the most reasonable way to implement them?
- **Surface Pattern Trap**: Are they stuck on keyword matching rather than understanding code flow and business logic?
- **Incomplete Repair Scope**: Are they fixing one instance but missing related patterns that need the same treatment?
- **Alternative Root Causes**: Have they considered explanations beyond what the issue description suggests?

**ITERATION EFFECTIVENESS & STRATEGY RIGIDITY CHECK:**
- **Progress Quality**: Are recent actions building meaningfully toward a solution or just variations of the same approach?
- **Strategic Coherence**: Is their method progression logical or are they switching approaches chaotically?
- **Context Retention**: Have they forgotten or ignored critical discoveries from earlier investigation phases?
- **Diminishing Returns**: Is their current path showing signs of being unproductive or stuck?

**REPAIR STRATEGY QUALITY CHECK:**
- **Root Cause vs Symptoms**: Is their proposed solution addressing fundamental causes or just patching symptoms?
- **Generalization vs Overfitting**: Will their fix work broadly or only for the specific reported case?
- **Evasive Repair Risk**: Are they using try-catch bandaids or condition bypasses instead of proper fixes?
- **Domain Compliance**: Does their approach follow framework conventions and architectural best practices?

**EXPERT INTERVENTION DECISION:**
Based on your analysis, provide:
- **Failure Mode Alert**: Which specific pattern(s) require immediate intervention
- **Course Correction**: Concrete steps to break out of problematic patterns
- **Strategic Guidance**: Whether to continue, modify approach, or reset strategy entirely
- **Success Path Redirect**: Specific next actions aligned with proven SWE-Bench methodologies

Focus on decisive intervention - identify the most critical issue and provide specific corrective guidance.
"""
        
        try:
            logger.info(f"[EXPERT_TIMING] Starting LLM call for passive check at turn {current_turn}")
            response = litellm_completion(
               model="openai/claude-3-5-sonnet-20241022",
                    api_key="",
                    base_url="",
                messages=[
                    {'role': 'system', 'content': self.system_message},
                    {'role': 'user', 'content': expert_query}
                ],
       
            )
            
            expert_response = response.choices[0].message.content.strip()
            logger.info(f"[EXPERT_TIMING] Completed LLM call for passive check at turn {current_turn}")
            logger.info(f"Generated passive expert response: {expert_response}")
            
            return expert_response
            
        except Exception as e:
            logger.error(f"Failed to generate expert response: {e}")
            return None
    
    # provide_passive_check method removed - now using agent-initiated pause mechanism
    
    def handle_expert_request(self, agent_message: MessageAction, state: State) -> str:

        current_iteration = self.get_current_iteration(state)
        

        interactions_text = self.get_real_time_context_from_state(state)
        
        prompt = f"""
**Active Expert Consultation - Iteration {current_iteration}**

**Original Issue**: {self.problem_statement}

**Agent's Specific Request**:
"{agent_message.content}"

**Complete Recent Context**:
{interactions_text}

**EXPERT CONSULTATION FRAMEWORK:**
As their senior expert, provide targeted guidance using failure mode awareness:

**IMMEDIATE QUESTION RESPONSE**:
- Address their specific concern with actionable guidance
- Validate or correct their understanding of the current situation

**FAILURE MODE RISK ASSESSMENT**:
- **Tunnel Vision Risk**: Are they trapped in assumptions from the issue description or surface pattern matching?
- **Iteration Effectiveness**: Is their current approach showing meaningful progress or signs of diminishing returns?
- **Strategy Quality**: Will their proposed direction lead to robust fixes or just symptom patching?

**EXPERT STRATEGIC GUIDANCE**:
- **Path Validation**: Is their current direction fundamentally sound for this type of problem?
- **Course Correction**: Should they continue, modify their approach, or consider a strategic reset?
- **Success Methodology**: What proven SWE-Bench patterns should guide their next steps?

**DECISIVE EXPERT RECOMMENDATION**:
Based on your analysis, provide:
- **Confidence Assessment**: GO/MODIFY/RESET decision on their current approach
- **Immediate Next Action**: Specific step they should take right now
- **Key Validation Points**: How they can verify they're on the right track
- **Critical Pitfalls**: What mistakes to avoid going forward

**RESPONSE TONE**: Be authoritative and specific. They need expert certainty to proceed confidently. Focus on breaking any failure patterns you detect and redirecting toward proven success methodologies.
        """
        
        return self._generate_expert_response(prompt, is_passive=False)
    
    def _generate_expert_response(self, prompt: str, is_passive: bool) -> str:

        temp_history = self.chat_history + [{'role': 'user', 'content': prompt}]
        
        try:
            response = litellm_completion(
                model=self.llm_config.model,
                messages=temp_history,
                api_key=self.llm_config.api_key.get_secret_value(),
                temperature=self.llm_config.temperature,
                base_url=self.llm_config.base_url,
            )
            
            reply = response.choices[0].message.content
            
            if is_passive:
                self.passive_check_count += 1

                logger.info(f"Passive expert check #{self.passive_check_count} generated successfully")
            else:
                self.active_request_count += 1
                logger.info(f"Active expert request #{self.active_request_count}: {reply}")
        
            structured_reply = self.structure_expert_response(reply, is_passive)
            return structured_reply
            
        except Exception as e:
            logger.error(f"Error generating expert response: {e}")
            return "Continue with your current approach. I'm unable to provide guidance at the moment."
    
    def structure_expert_response(self, raw_response: str, is_passive: bool) -> str:
   
        if is_passive:
            prefix = "**Passive Expert Review**:\n"
        else:
            prefix = "**Expert Consultation**:\n"
        
        # 确保包含关键部分
        if "**Assessment**" not in raw_response:
            raw_response = f"**Assessment**: {raw_response}"
        
        return f"{prefix}{raw_response}"



expert_judge = None


def get_expert_hybrid_instruction(instance: pd.Series, metadata: EvalMetadata) -> MessageAction:

    base_instruction = base_get_instruction(instance, metadata)

    enable_expert_requests = 'true'
    if enable_expert_requests:
        enhanced_content = base_instruction.content

        
        base_instruction.content = enhanced_content
    
    return base_instruction


def create_expert_hybrid_response_fn(expert_judge_instance: ExpertJudge):

    def get_expert_hybrid_response(state: State) -> str:

        current_iteration = expert_judge_instance.get_current_iteration(state)
        
  
        expert_judge_instance.fake_response_call_count += 1
        
        logger.info(f"[EXPERT_DEBUG] Expert response function called #{expert_judge_instance.fake_response_call_count}, iteration={current_iteration}")

        last_agent_message = state.get_last_agent_message()

        if last_agent_message and "PASSIVE CHECK REQUEST" in last_agent_message.content:
            logger.info(f"[PASSIVE_CHECK] Detected passive check request at iteration {current_iteration}")
            
 
            if expert_judge_instance.passive_check_count >= expert_judge_instance.max_checks:
                logger.info(f"[PASSIVE_CHECK] Max checks reached ({expert_judge_instance.passive_check_count}/{expert_judge_instance.max_checks})")
                return "Passive expert check limit reached. Please continue working independently."
            
            
            passive_message = expert_judge_instance.generate_passive_check_message(state, current_iteration)
            if passive_message:
          
                expert_judge_instance.passive_check_count += 1
                logger.info(f"[PASSIVE_CHECK] Generated passive expert response #{expert_judge_instance.passive_check_count}")
                return f"🤖 **AUTOMATIC PASSIVE CHECK** - Expert Review: {passive_message}\n\nPlease consider this expert guidance."
            else:
                logger.warning(f"[PASSIVE_CHECK] Failed to generate passive check message")
                return "Passive expert check temporarily unavailable. Please continue working."
        
 
        if last_agent_message and ("expert consultation" in last_agent_message.content.lower()or "professional guidance" in last_agent_message.content.lower()):
            logger.info(f"Active expert consultation detected: {last_agent_message.content}")
            expert_response = expert_judge_instance.handle_expert_request(last_agent_message, state)
            expert_judge_instance.active_request_count += 1 
            return f"**Expert Professional Guidance**: {expert_response}\n\nYou may now proceed with confidence based on this expert assessment."
        
        if last_agent_message and "[REQUEST_EXPERT]" in last_agent_message.content:
            expert_response = expert_judge_instance.handle_expert_request(last_agent_message, state)
            expert_judge_instance.active_request_count += 1 
            return f"**Expert Consultation**: {expert_response}\n\nProceed based on this expert guidance."
        
   
        if last_agent_message and ("would you like me to" in last_agent_message.content.lower() or 
                                  "please let me know if you" in last_agent_message.content.lower()):
            logger.info(f"Agent asked for continuation guidance, treating as active expert request")
            expert_response = expert_judge_instance.handle_expert_request(last_agent_message, state)
            expert_judge_instance.active_request_count += 1  
            return f"**Expert Guidance**: {expert_response}\n\nYes, proceed based on this assessment."

        logger.info(f"[EXPERT_DEBUG] No expert request conditions met, returning default continue message")
        return "Continue working on the current task."
    
    return get_expert_hybrid_response




def process_instance(
    instance: pd.Series,
    metadata: EvalMetadata,
    reset_logger: bool = True,
) -> EvalOutput:
    config = get_config(instance, metadata)
    

    problem_statement = instance.problem_statement
    

    expert_check_interval = int(os.environ.get('EXPERT_CHECK_INTERVAL', '10'))
    max_expert_checks = int(os.environ.get('MAX_EXPERT_CHECKS', '3'))
    
    expert_judge = ExpertJudge(
        problem_statement=problem_statement,
        check_interval=expert_check_interval,
        max_checks=max_expert_checks
    )

    main_llm_config = getattr(config, 'llm_config', None) or list(config.llms.values())[0]
    if hasattr(main_llm_config, 'log_completions_folder'):
        expert_judge.log_completions_folder = main_llm_config.log_completions_folder
    

    expert_response_fn = create_expert_hybrid_response_fn(expert_judge)

    # Setup logger
    if reset_logger:
        log_dir = os.path.join(metadata.eval_output_dir, 'infer_logs')
        reset_logger_for_multiprocessing(logger, instance.instance_id, log_dir)
    else:
        logger.info(f'Starting evaluation for instance {instance.instance_id}.')

    runtime = create_runtime(config)
    call_async_from_sync(runtime.connect)

    try:
        initialize_runtime(runtime, instance, metadata)

        message_action = get_expert_hybrid_instruction(instance, metadata)


        state: State | None = asyncio.run(
            run_controller_with_expert_hooks(
                config=config,
                initial_user_action=message_action,
                runtime=runtime,
                expert_judge=expert_judge,
                fake_user_response_fn=expert_response_fn,
            )
        )


        if (
            state
            and state.last_error
            and 'fatal error during agent execution' in state.last_error
            and 'stuck in a loop' not in state.last_error
        ):
            raise EvalException('Fatal error detected: ' + state.last_error)


        return_val = complete_runtime(runtime, instance)
        git_patch = return_val['git_patch']
        logger.info(
            f'Got git diff for instance {instance.instance_id}:\n--------\n{git_patch}\n--------'
        )
    finally:
        runtime.close()


    test_result = {
        'git_patch': git_patch,
    }

    if state is None:
        raise ValueError('State should not be None.')

    histories = [event_to_dict(event) for event in state.history]
    metrics = state.metrics.get() if state.metrics else None


    instruction = message_action.content
    if message_action.image_urls:
        instruction += (
            '\n\n<image_urls>' + '\n'.join(message_action.image_urls) + '</image_urls>'
        )
    output = EvalOutput(
        instance_id=instance.instance_id,
        instruction=instruction,
        instance=instance.to_dict(),
        test_result=test_result,
        metadata=metadata,
        history=histories,
        metrics=metrics,
        error=state.last_error if state and state.last_error else None,
    )
    

    actual_iterations = getattr(state, 'iteration_flag', None)
    if actual_iterations is not None:
        actual_iterations = actual_iterations.current_value
    else:
        actual_iterations = len([e for e in state.history if getattr(e, 'source', '') == 'agent'])
    
    logger.info(f"""
    Expert Hybrid Summary for {instance.instance_id}:
    - Passive Expert Checks: {expert_judge.passive_check_count}
    - Active Expert Requests: {expert_judge.active_request_count}
    - Total Controller Iterations: {actual_iterations}
    - Check Interval: {expert_judge.check_interval}
    - Max Checks: {expert_judge.max_checks}
    """)
    
    return output


if __name__ == '__main__':
    parser = get_parser()
    parser.add_argument(
        '--dataset',
        type=str,
        default='princeton-nlp/SWE-bench_Verified',
        help='dataset to evaluate on (default: SWE-bench_Verified)',
    )
    parser.add_argument(
        '--split',
        type=str,
        default='test',
        help='split to evaluate on',
    )
    parser.add_argument(
        '--expert-check-interval',
        type=int,
        default=10,
        help='Number of agent turns between passive expert checks'
    )
    parser.add_argument(
        '--max-expert-checks',
        type=int,
        default=3,
        help='Maximum number of passive expert checks per instance'
    )
    parser.add_argument(
        '--disable-expert-requests',
        action='store_true',
        help='Disable active expert requests from agent'
    )

    args, _ = parser.parse_known_args()
    

    os.environ['EXPERT_CHECK_INTERVAL'] = str(args.expert_check_interval)
    os.environ['MAX_EXPERT_CHECKS'] = str(args.max_expert_checks)
    os.environ['ENABLE_EXPERT_REQUESTS'] = str(not args.disable_expert_requests).lower()

    # Load dataset
    set_dataset_type(args.dataset)
    dataset = load_dataset(args.dataset, split=args.split)
    swe_bench_tests = filter_dataset(dataset.to_pandas(), 'instance_id')
    logger.info(
        f'Loaded dataset {args.dataset} with split {args.split}: {len(swe_bench_tests)} tasks'
    )
    
    llm_config = None
    if args.llm_config:
        llm_config = get_llm_config_arg(args.llm_config)
        llm_config.log_completions = True
        llm_config.modify_params = False

    if llm_config is None:
        raise ValueError(f'Could not find LLM config: --llm_config {args.llm_config}')

    # Get condenser config
    condenser_name = os.environ.get('EVAL_CONDENSER')
    if condenser_name:
        condenser_config = get_condenser_config_arg(condenser_name)
        if condenser_config is None:
            raise ValueError(
                f'Could not find Condenser config: EVAL_CONDENSER={condenser_name}'
            )
    else:
        condenser_config = NoOpCondenserConfig()
        logger.debug(
            'No Condenser config provided via EVAL_CONDENSER, using NoOpCondenser.'
        )

    # Prepare metadata
    details = {
        'mode': 'swe',  
        'expert_hybrid': True,  
        'expert_check_interval': args.expert_check_interval,
        'max_expert_checks': args.max_expert_checks,
        'expert_requests_enabled': not args.disable_expert_requests,
    }
    
    # Validate agent class exists
    openhands.agenthub.Agent.get_cls(args.agent_cls)

    dataset_description = (
        args.dataset.replace('/', '__') + '-' + args.split.replace('/', '__')
    )
    metadata = make_metadata(
        llm_config,
        dataset_description,
        args.agent_cls,
        args.max_iterations,
        args.eval_note,
        args.eval_output_dir,
        details=details,
        condenser_config=condenser_config,
    )

    output_file = os.path.join(metadata.eval_output_dir, 'output.jsonl')
    print(f'### OUTPUT FILE: {output_file} ###')
    
    logger.info(f"""
    Expert Hybrid Configuration:
    - Expert Check Interval: {args.expert_check_interval}
    - Max Expert Checks: {args.max_expert_checks}  
    - Expert Requests Enabled: {not args.disable_expert_requests}
    """)

    # 支持迭代评估模式
    ITERATIVE_EVAL_MODE = (
        os.environ.get('ITERATIVE_EVAL_MODE', 'false').lower() == 'true'
    )
    ITERATIVE_EVAL_MODE_MAX_ATTEMPTS = int(
        os.environ.get('ITERATIVE_EVAL_MODE_MAX_ATTEMPTS', '3')
    )

    if not ITERATIVE_EVAL_MODE:
        instances = prepare_dataset(swe_bench_tests, output_file, args.eval_n_limit)
        if len(instances) > 0 and hasattr(instances, 'columns'):
            if 'PASS_TO_PASS' in instances.columns and not isinstance(
                instances['PASS_TO_PASS'][instances['PASS_TO_PASS'].index[0]], str
            ):
                for col in ['PASS_TO_PASS', 'FAIL_TO_PASS']:
                    if col in instances.columns:
                        instances[col] = instances[col].apply(lambda x: str(x))
        
        run_evaluation(
            instances,
            metadata,
            output_file,
            args.eval_num_workers,
            process_instance,
            timeout_seconds=8 * 60 * 60, 
            max_retries=5,
        )
    else:

        critic = AgentFinishedCritic()
        def get_cur_output_file_path(attempt: int) -> str:
            return (
                f'{output_file.removesuffix(".jsonl")}.critic_attempt_{attempt}.jsonl'
            )

        eval_ids = None
        for attempt in range(1, ITERATIVE_EVAL_MODE_MAX_ATTEMPTS + 1):
            cur_output_file = get_cur_output_file_path(attempt)
            logger.info(
                f'Running evaluation with critic {critic.__class__.__name__} for attempt {attempt} of {ITERATIVE_EVAL_MODE_MAX_ATTEMPTS}.'
            )

            if attempt > 1 and metadata.llm_config.temperature == 0:
                logger.info(
                    f'Detected temperature is 0 for (>1) attempt {attempt}. Setting temperature to 0.1...'
                )
                metadata.llm_config.temperature = 0.1

            instances = prepare_dataset(
                swe_bench_tests, cur_output_file, args.eval_n_limit, eval_ids=eval_ids
            )
            if len(instances) > 0 and hasattr(instances, 'columns'):
                if 'PASS_TO_PASS' in instances.columns and not isinstance(
                    instances['PASS_TO_PASS'][instances['PASS_TO_PASS'].index[0]], str
                ):
                    for col in ['PASS_TO_PASS', 'FAIL_TO_PASS']:
                        if col in instances.columns:
                            instances[col] = instances[col].apply(lambda x: str(x))

            logger.info(
                f'Evaluating {len(instances)} instances for attempt {attempt}'
            )
            run_evaluation(
                instances,
                metadata,
                cur_output_file,
                args.eval_num_workers,
                process_instance,
                timeout_seconds=8 * 60 * 60,
                max_retries=5,
            )


            instances_failed = []
            logger.info(
                f'Use critic {critic.__class__.__name__} to check {len(instances)} instances for attempt {attempt}'
            )
            with open(cur_output_file, 'r') as f:
                for line in f:
                    instance = json.loads(line)
                    try:
                        history = [
                            event_from_dict(event) for event in instance['history']
                        ]
                        critic_result = critic.evaluate(
                            history, instance['test_result'].get('git_patch', '')
                        )
                        if not critic_result.success:
                            instances_failed.append(instance['instance_id'])
                    except Exception as e:
                        logger.error(
                            f'Error loading history for instance {instance["instance_id"]}: {e}'
                        )
                        instances_failed.append(instance['instance_id'])
            logger.info(
                f'{len(instances_failed)} instances failed the current attempt {attempt}: {instances_failed}'
            )
            eval_ids = instances_failed

            if len(instances_failed) == 0:
                break
        logger.info(
            'Aggregating results from all attempts into the original output file...'
        )
        fout = open(output_file, 'w')
        added_instance_ids = set()
        for attempt in reversed(range(1, ITERATIVE_EVAL_MODE_MAX_ATTEMPTS + 1)):
            cur_output_file = get_cur_output_file_path(attempt)
            if not os.path.exists(cur_output_file):
                logger.warning(
                    f'Intermediate output file {cur_output_file} does not exist. Skipping...'
                )
                continue

            with open(cur_output_file, 'r') as f:
                for line in f:
                    instance = json.loads(line)
                    if (
                        instance['instance_id'] not in added_instance_ids
                        and instance['test_result'].get('git_patch', '').strip()
                    ):
                        fout.write(line)
                        added_instance_ids.add(instance['instance_id'])
            logger.info(
                f'Aggregated instances from {cur_output_file}. Total instances added so far: {len(added_instance_ids)}'
            )
        fout.close()
        logger.info(
            f'Done! Total {len(added_instance_ids)} instances added to {output_file}'
        )