from flask import Flask, render_template, request, jsonify
import os
import json
import glob
import uuid

app = Flask(__name__)

# Data storage files
ANNOTATIONS_FILE = "annotations_openhands.json"
DATA_FOLDER = "extracted_log_openhands_points"
def load_annotations():
    """Load existing annotation data"""
    try:
        if os.path.exists(ANNOTATIONS_FILE):
            with open(ANNOTATIONS_FILE, 'r', encoding='utf-8') as f:
                annotations = json.load(f)
                # Auto-migrate data format
                return migrate_annotations_data(annotations)
    except Exception as e:
        print(f"Failed to load annotation file: {e}")
        # Backup corrupted file
        if os.path.exists(ANNOTATIONS_FILE):
            backup_file = ANNOTATIONS_FILE + '.backup'
            try:
                os.rename(ANNOTATIONS_FILE, backup_file)
                print(f"{backup_file}")
            except:
                pass
    return {}

def migrate_annotations_data(annotations):
    """Migrate old annotation data formats to the new format"""
    migrated = False
    
    for data_point_name, data_point_annotations in annotations.items():
        if isinstance(data_point_annotations, list):
      
            new_format = {}
            for annotation in data_point_annotations:
                if isinstance(annotation, dict):
                    action_index = str(annotation.get('action_index', 0))
                    if action_index not in new_format:
                        new_format[action_index] = []
                    
         
                    if 'id' not in annotation:
                        annotation['id'] = str(uuid.uuid4())
                    
                    new_format[action_index].append(annotation)
            
            annotations[data_point_name] = new_format
            migrated = True
        
        elif isinstance(data_point_annotations, dict):
     
            for action_index, action_annotations in data_point_annotations.items():
                if isinstance(action_annotations, dict) and 'category' in action_annotations:
 
                    if 'id' not in action_annotations:
                        action_annotations['id'] = str(uuid.uuid4())
                    annotations[data_point_name][action_index] = [action_annotations]
                    migrated = True
                elif isinstance(action_annotations, list):
   
                    for annotation in action_annotations:
                        if isinstance(annotation, dict) and 'id' not in annotation:
                            annotation['id'] = str(uuid.uuid4())
                            migrated = True
    
    if migrated:
        try:
            save_annotations(annotations)
         
        except Exception as e:
            print(f"{e}")
    
    return annotations

def save_annotations(annotations):

    with open(ANNOTATIONS_FILE, 'w', encoding='utf-8') as f:
        json.dump(annotations, indent=2, ensure_ascii=False, fp=f)

def load_report_data(folder_path):
 
    report_file = os.path.join(folder_path, 'report.json')
    if os.path.exists(report_file):
        try:
            with open(report_file, 'r', encoding='utf-8') as f:
                report_data = json.load(f)
            return report_data
        except Exception as e:
            print(f" {e}")
    return None

def extract_test_statistics(report_data, case_name):

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

    data_points = []
    

    for folder in glob.glob(os.path.join(DATA_FOLDER, '*')):
        if os.path.isdir(folder):
            folder_name = os.path.basename(folder)
            
            try:
     
                log_file = os.path.join(folder, f'{folder_name}.log')
                log_content = ""
                
                if os.path.exists(log_file):
                    with open(log_file, 'r', encoding='utf-8') as f:
                        log_content = f.read()
   
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
                print(f"读取数据点 {folder_name} 失败: {e}")
 
                data_points.append({
                    'name': folder_name,
                    'log_content': f"无法读取数据文件: {e}",
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

    actions = []
    

    if "### This is the" in log_content and "role:" in log_content:
        return parse_openhands_log(log_content)

    elif "### This is the" in log_content and "ACTION" in log_content:
        import re
        

        action_pattern = r'### This is the (\d+)(?:st|nd|rd|th) action: ACTION \([^)]+\):'
        matches = list(re.finditer(action_pattern, log_content))
        

        if matches:
            first_action_start = matches[0].start()

            thought_pattern = r'\nTHOUGHT:\n'
            first_thought_matches = list(re.finditer(thought_pattern, log_content[:first_action_start]))
            
            if first_thought_matches:
       
                initial_end = first_thought_matches[0].start()
            else:
 
                initial_end = first_action_start
                
            initial_content = log_content[:initial_end].strip()
        else:
            initial_content = log_content.strip()
            
        if initial_content:

            if initial_content.startswith("Initial prompt:"):
                initial_content = initial_content[len("Initial prompt:"):].strip()
            
            actions.append({
                'index': 0,
                'type': 'Initial Prompt',
                'content': initial_content,
                'line_start': 0,
                'line_end': len(initial_content.split('\n'))
            })

        for i, match in enumerate(matches):
            action_num = int(match.group(1)) - 1
            start_pos = match.start()

            thought_start = start_pos

            thought_pattern = r'\nTHOUGHT:\n'
            thought_matches = list(re.finditer(thought_pattern, log_content[:start_pos]))
            
            if thought_matches:

                last_thought = thought_matches[-1]
             
                if i == 0 or last_thought.start() > matches[i-1].start():
                    thought_start = last_thought.start()

            if i + 1 < len(matches):
                next_action_start = matches[i + 1].start()
      
                next_thought_matches = list(re.finditer(thought_pattern, log_content[start_pos:next_action_start]))
                if next_thought_matches:
       
                    end_pos = start_pos + next_thought_matches[0].start()
                else:
      
                    end_pos = next_action_start
            else:
                end_pos = len(log_content)
            

            full_content = log_content[thought_start:end_pos].strip()

            round_content = log_content[start_pos:end_pos].strip()
            

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
    
    elif "=== PATCH DIFF ===" in log_content or "=== GOLD DIFF ===" in log_content:
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
    else:

        actions.append({
            'index': 0,
            'type': 'Combined Content',
            'content': log_content,
            'line_start': 0,
            'line_end': len(log_content.split('\n'))
        })
    
    return actions

def parse_openhands_log(log_content):

    import re
    actions = []
    

    action_pattern = r'### This is the (\d+)(?:st|nd|rd|th) action:'
    matches = list(re.finditer(action_pattern, log_content))
    

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
    

    for i, match in enumerate(matches):
        action_num = int(match.group(1)) - 1 
        start_pos = match.start()
        

        if i + 1 < len(matches):
            end_pos = matches[i + 1].start()
        else:
            end_pos = len(log_content)
        

        action_content = log_content[start_pos:end_pos].strip()
        

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

@app.route('/')
def index():

    data_points = get_data_points()
    return render_template('index.html', data_points=data_points)

@app.route('/get_actions/<data_point_name>')
def get_actions(data_point_name):

    data_points = get_data_points()
    
    for dp in data_points:
        if dp['name'] == data_point_name:
            actions = parse_agent_log(dp['log_content'])
            return jsonify({'actions': actions})
    
    return jsonify({'error': 'Data point not found'}), 404

@app.route('/add_annotation', methods=['POST'])
def add_annotation():

    try:
        data = request.json
        if not data:
            return jsonify({'success': False, 'error': 'error'}), 400
            
        data_point_name = data.get('data_point_name')
        action_index = data.get('action_index')
        category = data.get('category')
        reason = data.get('reason')
        
        if not all([data_point_name, category, reason]) or action_index is None:
            return jsonify({'success': False, 'error': 'error'}), 400
        
        annotations = load_annotations()
        
   
        if data_point_name not in annotations:
            annotations[data_point_name] = {}
        
        if not isinstance(annotations[data_point_name], dict):
    
            old_annotations = annotations[data_point_name] if isinstance(annotations[data_point_name], list) else []
            annotations[data_point_name] = {}
            
            for old_ann in old_annotations:
                if isinstance(old_ann, dict):
                    old_action_index = str(old_ann.get('action_index', 0))
                    if old_action_index not in annotations[data_point_name]:
                        annotations[data_point_name][old_action_index] = []
                    annotations[data_point_name][old_action_index].append(old_ann)
        
        action_key = str(action_index)
        

        if action_key not in annotations[data_point_name]:
            annotations[data_point_name][action_key] = []

        if not isinstance(annotations[data_point_name][action_key], list):

            old_annotation = annotations[data_point_name][action_key]
            annotations[data_point_name][action_key] = [old_annotation] if isinstance(old_annotation, dict) else []
        

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
        print(f"{e}")
        print(f"{data_point_name}")
        print(f"{action_index}")
        try:
            annotations = load_annotations()
            print(f" {annotations.get(data_point_name, 'Not found')}")
        except:
            print("error loading annotations")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/delete_annotation', methods=['POST'])
def delete_annotation():

    try:
        data = request.json
        if not data:
            return jsonify({'success': False, 'error': 'error'}), 400
            
        data_point_name = data.get('data_point_name')
        action_index = data.get('action_index')
        annotation_id = data.get('annotation_id')
        
        if not data_point_name or action_index is None:
            return jsonify({'success': False, 'error': 'error'}), 400
        
        annotations = load_annotations()
        
        if data_point_name not in annotations:
            return jsonify({'success': False, 'error': 'error'}), 404
        

        if not isinstance(annotations[data_point_name], dict):
            return jsonify({'success': False, 'error': 'error'}), 400
        
        action_key = str(action_index)
        if action_key not in annotations[data_point_name]:
            return jsonify({'success': False, 'error': 'error'}), 404

        if not isinstance(annotations[data_point_name][action_key], list):

            old_annotation = annotations[data_point_name][action_key]
            annotations[data_point_name][action_key] = [old_annotation] if isinstance(old_annotation, dict) else []

        if annotation_id:
            action_annotations = annotations[data_point_name][action_key]
            annotations[data_point_name][action_key] = [
                ann for ann in action_annotations if ann.get('id') != annotation_id
            ]
 
            if not annotations[data_point_name][action_key]:
                del annotations[data_point_name][action_key]
        else:

            del annotations[data_point_name][action_key]
        

        if not annotations[data_point_name]:
            del annotations[data_point_name]
        
        save_annotations(annotations)
        return jsonify({'success': True})
        
    except Exception as e:
        print(f" {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/get_annotations/<data_point_name>')
def get_annotations(data_point_name):

    try:
        annotations = load_annotations()
        data_point_annotations = annotations.get(data_point_name, {})
        
        result = []
        

        if isinstance(data_point_annotations, list):

            for i, annotation in enumerate(data_point_annotations):
                result.append({
                    'id': annotation.get('id', str(i)),
                    'action_index': annotation.get('action_index', 0),
                    'category': annotation.get('category', ''),
                    'reason': annotation.get('reason', '')
                })
        elif isinstance(data_point_annotations, dict):

            for action_index, action_annotations in data_point_annotations.items():
                if isinstance(action_annotations, list):
 
                    for annotation in action_annotations:
                        result.append({
                            'id': annotation.get('id', ''),
                            'action_index': int(action_index),
                            'category': annotation.get('category', ''),
                            'reason': annotation.get('reason', '')
                        })
                elif isinstance(action_annotations, dict):
    
                    result.append({
                        'id': action_annotations.get('id', ''),
                        'action_index': int(action_index),
                        'category': action_annotations.get('category', ''),
                        'reason': action_annotations.get('reason', '')
                    })
        
        return jsonify(result)
        
    except Exception as e:
        print(f"{e}")
        print(f"{data_point_name}")
        print(f"{annotations.get(data_point_name, 'Not found')}")
        return jsonify([])  

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)