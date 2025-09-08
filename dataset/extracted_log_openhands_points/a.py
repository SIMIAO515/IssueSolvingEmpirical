import json
import os
import re
from pathlib import Path

def extract_text_from_content(content_list):
    """从content列表中提取text内容"""
    if not content_list or not isinstance(content_list, list):
        return ""
    
    for item in content_list:
        if isinstance(item, dict) and item.get("type") == "text":
            return item.get("text", "")
    return ""

def extract_arguments_from_tool_calls(tool_calls):
    """从tool_calls中提取arguments内容"""
    if not tool_calls or not isinstance(tool_calls, list):
        return []
    
    arguments_list = []
    for tool_call in tool_calls:
        if isinstance(tool_call, dict) and "function" in tool_call:
            function = tool_call["function"]
            if isinstance(function, dict) and "arguments" in function:
                arguments_list.append(function["arguments"])
    return arguments_list

def convert_json_to_log(json_file_path, output_file_path):
    """将JSON文件转换为log格式"""
    try:
        with open(json_file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        if not isinstance(data, list):
            print(f"警告: {json_file_path} 不是list格式")
            return False
        
        log_content = []
        action_count = 1
        
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                continue
                
            role = item.get("role", "")
            content = item.get("content", [])
            
            if role == "system":
                text = extract_text_from_content(content)
                if text:
                    log_content.append(f"role: system\n{text}")
            
            elif role == "user":
                text = extract_text_from_content(content)
                if text:
                    log_content.append(f"role: user\n{text}")
            
            elif role == "assistant":
                text = extract_text_from_content(content)
                tool_calls = item.get("tool_calls", [])
                
                if text:
                    log_content.append(f"THOUGHT:\n{text}")
                
                # 处理tool_calls
                arguments_list = extract_arguments_from_tool_calls(tool_calls)
                for arguments in arguments_list:
                    log_content.append(f"### This is the {get_ordinal(action_count)} action:\n{arguments}")
                    action_count += 1
            
            elif role == "tool":
                text = extract_text_from_content(content)
                if text:
                    log_content.append(f"{text}")
        
        # 写入输出文件
        with open(output_file_path, 'w', encoding='utf-8') as f:
            f.write('\n\n'.join(log_content))
        
        return True
        
    except Exception as e:
        print(f"处理文件 {json_file_path} 时出错: {str(e)}")
        return False

def get_ordinal(n):
    """将数字转换为序数词（1st, 2nd, 3rd, 4th等）"""
    if 10 <= n % 100 <= 20:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return f"{n}{suffix}"

def batch_process_directory(root_directory):
    """批量处理目录下的所有JSON文件"""
    root_path = Path(root_directory)
    
    if not root_path.exists():
        print(f"目录不存在: {root_directory}")
        return
    
    processed_count = 0
    failed_count = 0
    
    # 遍历所有子目录
    for subdir in root_path.iterdir():
        if subdir.is_dir():
            # 在每个子目录中查找JSON文件
            json_files = list(subdir.glob("*.json"))
            
            for json_file in json_files:
                # 生成输出文件路径（同一目录下，扩展名改为.log）
                output_file = json_file.with_suffix('.log')
                
                print(f"正在处理: {json_file}")
                
                if convert_json_to_log(json_file, output_file):
                    print(f"成功转换: {json_file} -> {output_file}")
                    processed_count += 1
                else:
                    print(f"转换失败: {json_file}")
                    failed_count += 1
    
    print(f"\n批量处理完成!")
    print(f"成功处理: {processed_count} 个文件")
    print(f"处理失败: {failed_count} 个文件")

def main():
    # 设置根目录路径
    root_directory = r"C:\Users\31051\Desktop\analusis_swe\extracted_data_points_openhands"
    
    print(f"开始批量处理目录: {root_directory}")
    batch_process_directory(root_directory)

if __name__ == "__main__":
    main()