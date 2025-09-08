#!/usr/bin/env python3
"""
Failure Analysis Viewer for LLM Agent Research
=====================================

A Flask-based web application for viewing and annotating failure modes in LLM agents.
This tool supports interactive analysis of agent logs and systematic failure classification.

Usage:
    python failure_analysis_viewer.py [OPTIONS]

Arguments:
    --agent AGENT       Agent type to analyze (openhands|agentless|tools) [default: openhands]
    --port PORT         Port to run the web server on [default: 5000]
    --host HOST         Host to bind the server to [default: 0.0.0.0]
    --debug             Enable debug mode
    --annotations FILE  Custom annotations file path
    --data-folder PATH  Custom data folder path

Examples:
    # Run with default settings (OpenHands agent)
    python failure_analysis_viewer.py
    
    # Analyze Agentless agent data
    python failure_analysis_viewer.py --agent agentless
    
    # Run on custom port with debug mode
    python failure_analysis_viewer.py --port 8080 --debug
    
    # Use custom data paths
    python failure_analysis_viewer.py --annotations my_annotations.json --data-folder my_data/
"""

from flask import Flask, render_template, request, jsonify
import os
import json
import glob
import uuid
import argparse
import sys

app = Flask(__name__)

# Global configuration
CONFIG = {
    'annotations_file': 'annotations_openhands.json',
    'data_folder': 'extracted_log_openhands_points',
    'agent_type': 'openhands'
}

def load_annotations():
    """Load existing annotation data"""
    try:
        if os.path.exists(CONFIG['annotations_file']):
            with open(CONFIG['annotations_file'], 'r', encoding='utf-8') as f:
                annotations = json.load(f)
                # Auto-migrate data format
                return migrate_annotations_data(annotations)
    except Exception as e:
        print(f"Failed to load annotation file: {e}")
        # Backup corrupted file
        if os.path.exists(CONFIG['annotations_file']):
            backup_file = CONFIG['annotations_file'] + '.backup'
            try:
                os.rename(CONFIG['annotations_file'], backup_file)
                print(f"Backed up corrupted file to: {backup_file}")
            except:
                pass
    return {}

def migrate_annotations_data(annotations):
    """Migrate old annotation data formats to the new format"""
    migrated = False
    
    for data_point_name, data_point_annotations in annotations.items():
        if isinstance(data_point_annotations, list):
            # Convert old list format to new dict format
            new_format = {}
            for annotation in data_point_annotations:
                if isinstance(annotation, dict):
                    action_index = str(annotation.get('action_index', 0))
                    if action_index not in new_format:
                        new_format[action_index] = []
                    
                    # Ensure each annotation has a unique ID
                    if 'id' not in annotation:
                        annotation['id'] = str(uuid.uuid4())
                    
                    new_format[action_index].append(annotation)
            
            annotations[data_point_name] = new_format
            migrated = True
        
        elif isinstance(data_point_annotations, dict):
            # Check for old single-annotation format per action
            for action_index, action_annotations in data_point_annotations.items():
                if isinstance(action_annotations, dict) and 'category' in action_annotations:
                    # Convert single annotation to list format
                    if 'id' not in action_annotations:
                        action_annotations['id'] = str(uuid.uuid4())
                    annotations[data_point_name][action_index] = [action_annotations]
                    migrated = True
                elif isinstance(action_annotations, list):
                    # Ensure all annotations have IDs
                    for annotation in action_annotations:
                        if isinstance(annotation, dict) and 'id' not in annotation:
                            annotation['id'] = str(uuid.uuid4())
                            migrated = True
    
    if migrated:
        try:
            save_annotations(annotations)
            print("Successfully migrated annotations data format")
        except Exception as e:
            print(f"Failed to save migrated annotations: {e}")
    
    return annotations

def save_annotations(annotations):
    """Save annotations to file"""
    with open(CONFIG['annotations_file'], 'w', encoding='utf-8') as f:
        json.dump(annotations, f, indent=2, ensure_ascii=False)

def load_report_data(folder_path):
    """Load report data from folder"""
    report_file = os.path.join(folder_path, 'report.json')
    if os.path.exists(report_file):
        try:
            with open(report_file, 'r', encoding='utf-8') as f:
                report_data = json.load(f)
            return report_data
        except Exception as e:
            print(f"Failed to load report data: {e}")
    return None

def extract_test_statistics(report_data, case_name):
    """Extract test statistics from report data"""
    if not report_data or case_name not in report_data:
        return {
            'success': None,
            'pass_to_pass': 0,
            'pass_to_fail': 0,
            'fail_to_pass': 0,
            'fail_to_fail': 0,
            'test_failures': 0,
            'difficulty': 'Unknown'
        }
    
    case_data = report_data[case_name]
    tests_status = case_data.get('tests_status', {})
    
    # Count test transitions
    pass_to_pass = len(tests_status.get('PASS_TO_PASS', {}).get('success', [])) + len(tests_status.get('PASS_TO_PASS', {}).get('failure', []))
    pass_to_fail = len(tests_status.get('PASS_TO_FAIL', {}).get('success', [])) + len(tests_status.get('PASS_TO_FAIL', {}).get('failure', []))
    fail_to_pass = len(tests_status.get('FAIL_TO_PASS', {}).get('success', [])) + len(tests_status.get('FAIL_TO_PASS', {}).get('failure', []))
    fail_to_fail = len(tests_status.get('FAIL_TO_FAIL', {}).get('success', [])) + len(tests_status.get('FAIL_TO_FAIL', {}).get('failure', []))

    test_failures = (
        len(tests_status.get('FAIL_TO_PASS', {}).get('failure', [])) +
        len(tests_status.get('PASS_TO_PASS', {}).get('failure', [])) +
        len(tests_status.get('FAIL_TO_FAIL', {}).get('failure', [])) +
        len(tests_status.get('PASS_TO_FAIL', {}).get('failure', []))
    )
    
    return {
        'success': case_data.get('resolved', False),
        'pass_to_pass': pass_to_pass,
        'pass_to_fail': pass_to_fail,
        'fail_to_pass': fail_to_pass,
        'fail_to_fail': fail_to_fail,
        'test_failures': test_failures,
        'difficulty': case_data.get('difficulty', 'Unknown')
    }

def get_data_points():
    """Retrieve all data points from the data folder"""
    data_points = []
    
    # Scan for data folders
    for folder in glob.glob(os.path.join(CONFIG['data_folder'], '*')):
        if os.path.isdir(folder):
            folder_name = os.path.basename(folder)
            
            try:
                # Try to load log file
                log_file = os.path.join(folder, f'{folder_name}.log')
                log_content = ""
                
                if os.path.exists(log_file):
                    with open(log_file, 'r', encoding='utf-8') as f:
                        log_content = f.read()
                
                # Fallback to patch/gold diff if no log available
                if not log_content:
                    patch_file = os.path.join(folder, 'patch.diff')
                    gold_file = os.path.join(folder, 'gold.diff')
                    patch_diff = ""
                    gold_diff = ""
                    
                    if os.path.exists(patch_file):
                        with open(patch_file, 'r', encoding='utf-8') as f:
                            patch_diff = f.read()
                    
                    if os.path.exists(gold_file):
                        with open(gold_file, 'r', encoding='utf-8') as f:
                            gold_diff = f.read()
                    
                    log_content = f"=== PATCH DIFF ===\n{patch_diff}\n\n=== GOLD DIFF ===\n{gold_diff}"
                
                # Load report data and extract statistics
                report_data = load_report_data(folder)
                test_stats = extract_test_statistics(report_data, folder_name)
                
                hierarchy_path = folder_name.split('__')[0] if '__' in folder_name else folder_name
                
                data_points.append({
                    'name': folder_name,
                    'log_content': log_content,
                    'folder_path': folder,
                    'success': test_stats['success'],
                    'pass_to_pass': test_stats['pass_to_pass'],
                    'pass_to_fail': test_stats['pass_to_fail'],
                    'fail_to_pass': test_stats['fail_to_pass'],
                    'fail_to_fail': test_stats['fail_to_fail'],
                    'test_failures': test_stats['test_failures'],
                    'difficulty': test_stats['difficulty'],
                    'hierarchy_path': hierarchy_path
                })
                
            except Exception as e:
                print(f"Failed to read data point {folder_name}: {e}")
                # Add placeholder data point
                data_points.append({
                    'name': folder_name,
                    'log_content': f"Unable to read data file: {e}",
                    'folder_path': folder,
                    'success': None,
                    'pass_to_pass': 0,
                    'pass_to_fail': 0,
                    'fail_to_pass': 0,
                    'fail_to_fail': 0,
                    'test_failures': 0,
                    'difficulty': 'Unknown',
                    'hierarchy_path': folder_name
                })
    
    return data_points

def parse_agent_log(log_content):
    """Parse agent log content into structured actions"""
    actions = []
    
    # Detect log format and parse accordingly
    if "### This is the" in log_content and "role:" in log_content:
        return parse_openhands_log(log_content)
    elif "### This is the" in log_content and "ACTION" in log_content:
        return parse_tools_claude_log(log_content)
    elif "=== PATCH DIFF ===" in log_content or "=== GOLD DIFF ===" in log_content:
        return parse_diff_content(log_content)
    else:
        # Default: treat as single content block
        actions.append({
            'index': 0,
            'type': 'Combined Content',
            'content': log_content,
            'line_start': 0,
            'line_end': len(log_content.split('\n'))
        })
    
    return actions

def parse_tools_claude_log(log_content):
    """Parse Tools Claude format logs"""
    import re
    actions = []
    
    # Extract initial content before first action
    action_pattern = r'### This is the (\d+)(?:st|nd|rd|th) action: ACTION \([^)]+\):'
    matches = list(re.finditer(action_pattern, log_content))
    
    # Add initial prompt if present
    if matches:
        first_action_start = matches[0].start()
        # Look for THOUGHT patterns before first action
        thought_pattern = r'\nTHOUGHT:\n'
        first_thought_matches = list(re.finditer(thought_pattern, log_content[:first_action_start]))
        
        if first_thought_matches:
            # End before first thought if found
            initial_end = first_thought_matches[0].start()
        else:
            # Otherwise end at first action
            initial_end = first_action_start
            
        initial_content = log_content[:initial_end].strip()
    else:
        initial_content = log_content.strip()
        
    if initial_content:
        # Clean up initial prompt marker
        if initial_content.startswith("Initial prompt:"):
            initial_content = initial_content[len("Initial prompt:"):].strip()
        
        actions.append({
            'index': 0,
            'type': 'Initial Prompt',
            'content': initial_content,
            'line_start': 0,
            'line_end': len(initial_content.split('\n'))
        })

    # Parse individual actions
    for i, match in enumerate(matches):
        action_num = int(match.group(1)) - 1
        start_pos = match.start()

        # Find start position (may include preceding THOUGHT)
        thought_start = start_pos
        # Look for THOUGHT pattern before this action
        thought_pattern = r'\nTHOUGHT:\n'
        thought_matches = list(re.finditer(thought_pattern, log_content[:start_pos]))
        
        if thought_matches:
            # Get the last THOUGHT before this action
            last_thought = thought_matches[-1]
            # Only include if it's after the previous action or first action
            if i == 0 or last_thought.start() > matches[i-1].start():
                thought_start = last_thought.start()

        # Find end position
        if i + 1 < len(matches):
            next_action_start = matches[i + 1].start()
            # Look for THOUGHT before next action
            next_thought_matches = list(re.finditer(thought_pattern, log_content[start_pos:next_action_start]))
            if next_thought_matches:
                # End before the thought of next action
                end_pos = start_pos + next_thought_matches[0].start()
            else:
                # End at next action
                end_pos = next_action_start
        else:
            end_pos = len(log_content)
        
        # Extract full content including THOUGHT if present
        full_content = log_content[thought_start:end_pos].strip()
        # Extract just action content for type detection
        round_content = log_content[start_pos:end_pos].strip()
        
        # Determine action type based on content
        action_type = "Unknown Action"
        if "str_replace_editor" in round_content:
            action_type = "Code Editor"
        elif "bash" in round_content.lower():
            action_type = "Bash Command"  
        elif "python" in round_content.lower():
            action_type = "Python Execution"
        else:
            action_type = "Action"
        
        actions.append({
            'index': action_num,
            'type': action_type,
            'content': full_content,
            'line_start': 0,
            'line_end': len(full_content.split('\n'))
        })
    
    return actions

def parse_diff_content(log_content):
    """Parse diff content into separate patch and gold sections"""
    actions = []
    parts = log_content.split('=== GOLD DIFF ===')
    
    if len(parts) >= 2:
        patch_content = parts[0].replace('=== PATCH DIFF ===', '').strip()
        gold_content = parts[1].strip()
        
        if patch_content:
            actions.append({
                'index': 0,
                'type': 'Patch Diff',
                'content': patch_content,
                'line_start': 0,
                'line_end': len(patch_content.split('\n'))
            })
        
        if gold_content:
            actions.append({
                'index': 1,
                'type': 'Gold Diff',
                'content': gold_content,
                'line_start': 0,
                'line_end': len(gold_content.split('\n'))
            })
    
    return actions

def parse_openhands_log(log_content):
    """Parse OpenHands format logs"""
    import re
    actions = []
    
    # Find action boundaries
    action_pattern = r'### This is the (\d+)(?:st|nd|rd|th) action:'
    matches = list(re.finditer(action_pattern, log_content))
    
    # Add initial content if present
    if matches:
        initial_end = matches[0].start()
        initial_content = log_content[:initial_end].strip()
        
        if initial_content:
            actions.append({
                'index': 0,
                'type': 'Initial Prompt',
                'content': initial_content,
                'line_start': 0,
                'line_end': len(initial_content.split('\n'))
            })
    
    # Parse individual actions
    for i, match in enumerate(matches):
        action_num = int(match.group(1)) - 1  # Convert to 0-based indexing
        start_pos = match.start()
        
        # Find end position
        if i + 1 < len(matches):
            end_pos = matches[i + 1].start()
        else:
            end_pos = len(log_content)
        
        # Extract action content
        action_content = log_content[start_pos:end_pos].strip()
        
        # Determine action type based on content patterns
        action_type = "Unknown Action"
        if '"command":' in action_content:
            if '"ls"' in action_content or '"find"' in action_content:
                action_type = "File System"
            elif '"cat"' in action_content or '"head"' in action_content or '"tail"' in action_content:
                action_type = "File Read"
            elif '"str_replace_editor"' in action_content or '"edit"' in action_content:
                action_type = "Code Editor"
            elif '"python"' in action_content:
                action_type = "Python Execution"
            else:
                action_type = "Command"
        elif 'THOUGHT:' in action_content:
            action_type = "Reasoning"
        
        actions.append({
            'index': action_num,
            'type': action_type,
            'content': action_content,
            'line_start': 0,
            'line_end': len(action_content.split('\n'))
        })
    
    return actions

# Flask Routes

@app.route('/')
def index():
    """Main page showing all data points"""
    data_points = get_data_points()
    return render_template('index.html', data_points=data_points)

@app.route('/get_actions/<data_point_name>')
def get_actions(data_point_name):
    """Get parsed actions for a specific data point"""
    data_points = get_data_points()
    
    for dp in data_points:
        if dp['name'] == data_point_name:
            actions = parse_agent_log(dp['log_content'])
            return jsonify({'actions': actions})
    
    return jsonify({'error': 'Data point not found'}), 404

@app.route('/add_annotation', methods=['POST'])
def add_annotation():
    """Add a new annotation"""
    try:
        data = request.json
        if not data:
            return jsonify({'success': False, 'error': 'Invalid request data'}), 400
            
        data_point_name = data.get('data_point_name')
        action_index = data.get('action_index')
        category = data.get('category')
        reason = data.get('reason')
        
        if not all([data_point_name, category, reason]) or action_index is None:
            return jsonify({'success': False, 'error': 'Missing required fields'}), 400
        
        annotations = load_annotations()
        
        # Initialize data point if needed
        if data_point_name not in annotations:
            annotations[data_point_name] = {}
        
        if not isinstance(annotations[data_point_name], dict):
            # Convert old format if needed
            old_annotations = annotations[data_point_name] if isinstance(annotations[data_point_name], list) else []
            annotations[data_point_name] = {}
            
            for old_ann in old_annotations:
                if isinstance(old_ann, dict):
                    old_action_index = str(old_ann.get('action_index', 0))
                    if old_action_index not in annotations[data_point_name]:
                        annotations[data_point_name][old_action_index] = []
                    annotations[data_point_name][old_action_index].append(old_ann)
        
        action_key = str(action_index)
        
        # Initialize action annotations list
        if action_key not in annotations[data_point_name]:
            annotations[data_point_name][action_key] = []

        if not isinstance(annotations[data_point_name][action_key], list):
            # Convert old single annotation format
            old_annotation = annotations[data_point_name][action_key]
            annotations[data_point_name][action_key] = [old_annotation] if isinstance(old_annotation, dict) else []
        
        # Create new annotation
        annotation_data = {
            'id': str(uuid.uuid4()),
            'action_index': action_index,
            'category': category,
            'reason': reason
        }
        
        annotations[data_point_name][action_key].append(annotation_data)
        
        save_annotations(annotations)
        
        return jsonify({'success': True})
        
    except Exception as e:
        print(f"Error adding annotation: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/delete_annotation', methods=['POST'])
def delete_annotation():
    """Delete an annotation"""
    try:
        data = request.json
        if not data:
            return jsonify({'success': False, 'error': 'Invalid request data'}), 400
            
        data_point_name = data.get('data_point_name')
        action_index = data.get('action_index')
        annotation_id = data.get('annotation_id')
        
        if not data_point_name or action_index is None:
            return jsonify({'success': False, 'error': 'Missing required fields'}), 400
        
        annotations = load_annotations()
        
        if data_point_name not in annotations:
            return jsonify({'success': False, 'error': 'Data point not found'}), 404
        
        # Ensure proper format
        if not isinstance(annotations[data_point_name], dict):
            return jsonify({'success': False, 'error': 'Invalid annotation format'}), 400
        
        action_key = str(action_index)
        if action_key not in annotations[data_point_name]:
            return jsonify({'success': False, 'error': 'Action not found'}), 404

        if not isinstance(annotations[data_point_name][action_key], list):
            # Convert old format
            old_annotation = annotations[data_point_name][action_key]
            annotations[data_point_name][action_key] = [old_annotation] if isinstance(old_annotation, dict) else []

        if annotation_id:
            # Remove specific annotation by ID
            action_annotations = annotations[data_point_name][action_key]
            annotations[data_point_name][action_key] = [
                ann for ann in action_annotations if ann.get('id') != annotation_id
            ]
            # Clean up empty action
            if not annotations[data_point_name][action_key]:
                del annotations[data_point_name][action_key]
        else:
            # Remove all annotations for this action
            del annotations[data_point_name][action_key]
        
        # Clean up empty data point
        if not annotations[data_point_name]:
            del annotations[data_point_name]
        
        save_annotations(annotations)
        return jsonify({'success': True})
        
    except Exception as e:
        print(f"Error deleting annotation: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/get_annotations/<data_point_name>')
def get_annotations(data_point_name):
    """Get all annotations for a data point"""
    try:
        annotations = load_annotations()
        data_point_annotations = annotations.get(data_point_name, {})
        
        result = []
        
        # Handle different annotation formats
        if isinstance(data_point_annotations, list):
            # Old list format
            for i, annotation in enumerate(data_point_annotations):
                result.append({
                    'id': annotation.get('id', str(i)),
                    'action_index': annotation.get('action_index', 0),
                    'category': annotation.get('category', ''),
                    'reason': annotation.get('reason', '')
                })
        elif isinstance(data_point_annotations, dict):
            # New dict format
            for action_index, action_annotations in data_point_annotations.items():
                if isinstance(action_annotations, list):
                    # Multiple annotations per action
                    for annotation in action_annotations:
                        result.append({
                            'id': annotation.get('id', ''),
                            'action_index': int(action_index),
                            'category': annotation.get('category', ''),
                            'reason': annotation.get('reason', '')
                        })
                elif isinstance(action_annotations, dict):
                    # Single annotation per action (old format)
                    result.append({
                        'id': action_annotations.get('id', ''),
                        'action_index': int(action_index),
                        'category': action_annotations.get('category', ''),
                        'reason': action_annotations.get('reason', '')
                    })
        
        return jsonify(result)
        
    except Exception as e:
        print(f"Error getting annotations: {e}")
        return jsonify([])  # Return empty list on error

def setup_config(args):
    """Setup configuration based on command line arguments"""
    CONFIG['agent_type'] = args.agent
    
    # Set default paths based on agent type
    if args.agent == 'agentless':
        CONFIG['annotations_file'] = args.annotations or 'annotations_agentless.json'
        CONFIG['data_folder'] = args.data_folder or 'extracted_log_agentless_points'
    elif args.agent == 'tools':
        CONFIG['annotations_file'] = args.annotations or 'annotations_tools.json'
        CONFIG['data_folder'] = args.data_folder or 'extracted_log_tools-claude_points'
    else:  # openhands (default)
        CONFIG['annotations_file'] = args.annotations or 'annotations_openhands.json'
        CONFIG['data_folder'] = args.data_folder or 'extracted_log_openhands_points'
    
    # Override with custom paths if provided
    if args.annotations:
        CONFIG['annotations_file'] = args.annotations
    if args.data_folder:
        CONFIG['data_folder'] = args.data_folder

def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='Failure Analysis Viewer for LLM Agent Research',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                                    # Run with default settings
  %(prog)s --agent agentless                  # Analyze Agentless agent data
  %(prog)s --port 8080 --debug               # Custom port with debug mode
  %(prog)s --annotations my.json --data-folder my_data/  # Custom paths
        """
    )
    
    parser.add_argument('--agent', choices=['openhands', 'agentless', 'tools'],
                        default='openhands',
                        help='Agent type to analyze (default: openhands)')
    parser.add_argument('--port', type=int, default=5000,
                        help='Port to run the web server on (default: 5000)')
    parser.add_argument('--host', default='0.0.0.0',
                        help='Host to bind the server to (default: 0.0.0.0)')
    parser.add_argument('--debug', action='store_true',
                        help='Enable debug mode')
    parser.add_argument('--annotations',
                        help='Custom annotations file path')
    parser.add_argument('--data-folder',
                        help='Custom data folder path')
    
    args = parser.parse_args()
    
    # Setup configuration
    setup_config(args)
    
    # Validate paths
    if not os.path.exists(CONFIG['data_folder']):
        print(f"Error: Data folder '{CONFIG['data_folder']}' not found")
        print(f"Make sure you're running from the correct directory or specify --data-folder")
        sys.exit(1)
    
    print(f"Failure Analysis Viewer")
    print(f"Agent type: {CONFIG['agent_type']}")
    print(f"Data folder: {CONFIG['data_folder']}")
    print(f"Annotations file: {CONFIG['annotations_file']}")
    print(f"Starting server at http://{args.host}:{args.port}")
    
    # Run the Flask application
    app.run(debug=args.debug, host=args.host, port=args.port)

if __name__ == '__main__':
    main()