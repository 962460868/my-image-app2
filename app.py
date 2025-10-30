import streamlit as st
import requests
import time
import io
from PIL import Image
from datetime import datetime
import threading
import base64
import copy
import json

# 页面配置
st.set_page_config(
    page_title="RunningHub AI - 智能图片优化工具 (调试版)",
    page_icon="🎨",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 自定义CSS样式
st.markdown("""
<style>
    .main {
        background-color: #f5f7fa;
    }
    .stButton>button {
        width: 100%;
        border-radius: 8px;
        height: 3em;
        background-color: #3498db;
        color: white;
        font-weight: bold;
    }
    .stButton>button:hover {
        background-color: #2980b9;
    }
    .debug-log {
        background-color: #f8f9fa;
        border: 1px solid #dee2e6;
        border-radius: 5px;
        padding: 10px;
        margin: 10px 0;
        font-family: monospace;
        font-size: 12px;
        overflow-x: auto;
    }
    .task-card {
        background-color: white;
        border-radius: 10px;
        padding: 1.5rem;
        margin: 1rem 0;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    }
    .success-badge {
        color: #27ae60;
        font-weight: bold;
    }
    .error-badge {
        color: #e74c3c;
        font-weight: bold;
    }
    .processing-badge {
        color: #f39c12;
        font-weight: bold;
    }
    .info-badge {
        color: #17a2b8;
        font-weight: bold;
    }
</style>
""", unsafe_allow_html=True)

# 初始化 session_state
if 'tasks' not in st.session_state:
    st.session_state.tasks = []
if 'processing_count' not in st.session_state:
    st.session_state.processing_count = 0
if 'task_counter' not in st.session_state:
    st.session_state.task_counter = 0
if 'debug_logs' not in st.session_state:
    st.session_state.debug_logs = []

# 配置常量
MAX_CONCURRENT = 3
API_KEY = "c95f4c4d2703479abfbc55eefeb9bb71"
WEBAPP_ID = "1947599512657453057"
NODE_INFO = [
    {"nodeId": "38", "fieldName": "image", "fieldValue": "placeholder.png", "description": "图片输入"},
    {"nodeId": "60", "fieldName": "text", "fieldValue": "8k, high quality, high detail", "description": "正向提示词补充"},
    {"nodeId": "4", "fieldName": "text", "fieldValue": "色调艳丽,过曝,静态,细节模糊不清,字幕,风格,作品,画作,画面,静止,整体发灰,最差质量,低质量,JPEG压缩残留,丑陋的,残缺的,多余的手指,画得不好的手部,画得不好的脸部,畸形的,毁容的,形态畸形的肢体,手指融合,静止不动的画面,悲乱的背景,三条腿,背景人很多,倒着走", "description": "反向提示词"}
]

class TaskItem:
    """任务项类"""
    def __init__(self, task_id, file_data, file_name):
        self.task_id = task_id
        self.file_data = file_data
        self.file_name = file_name
        self.status = "QUEUED"
        self.progress = 0
        self.result_url = None
        self.result_data = None
        self.error_message = None
        self.api_task_id = None
        self.created_at = datetime.now()
        self.start_time = None
        self.elapsed_time = None
        self.debug_info = []  # 存储调试信息

def add_debug_log(task, message):
    """添加调试日志"""
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    log_entry = f"[{timestamp}] Task-{task.task_id}: {message}"
    task.debug_info.append(log_entry)
    print(log_entry)  # 也打印到控制台

def upload_file(file_data, file_name, api_key, task):
    """上传文件到服务器"""
    url = 'https://www.runninghub.cn/task/openapi/upload'
    
    add_debug_log(task, f"📤 开始上传文件: {file_name}, 大小: {len(file_data)} bytes")
    
    files = {'file': (file_name, file_data)}
    data = {'apiKey': api_key, 'fileType': 'image'}
    
    add_debug_log(task, f"上传URL: {url}")
    add_debug_log(task, f"上传参数: fileType=image")
    
    response = requests.post(url, files=files, data=data, timeout=60)
    response.raise_for_status()
    
    response_data = response.json()
    add_debug_log(task, f"📥 上传响应: {json.dumps(response_data, ensure_ascii=False)}")
    
    if response_data.get("code") == 0:
        uploaded_filename = response_data['data']['fileName']
        add_debug_log(task, f"✅ 文件上传成功! 服务器文件名: {uploaded_filename}")
        return uploaded_filename
    else:
        error_msg = f"图片上传失败: {response_data.get('msg', '未知错误')}"
        add_debug_log(task, f"❌ {error_msg}")
        raise Exception(error_msg)

def run_task(api_key, webapp_id, node_info_list, task):
    """发起任务"""
    run_url = 'https://www.runninghub.cn/task/openapi/run'
    headers = {'Content-Type': 'application/json'}
    payload = {
        "apiKey": api_key,
        "webappId": webapp_id,
        "nodeInfoList": node_info_list
    }
    
    add_debug_log(task, f"🚀 发起任务到: {run_url}")
    add_debug_log(task, f"请求头: {json.dumps(headers, ensure_ascii=False)}")
    add_debug_log(task, f"📦 完整Payload:")
    add_debug_log(task, json.dumps(payload, ensure_ascii=False, indent=2))
    
    # 特别检查图片节点
    image_node = next((n for n in node_info_list if n["nodeId"] == "38"), None)
    if image_node:
        add_debug_log(task, f"🖼️ 图片节点(38)的值: {image_node['fieldValue']}")
    else:
        add_debug_log(task, "⚠️ 警告: 未找到图片节点(nodeId=38)")
    
    response = requests.post(run_url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    
    run_data = response.json()
    add_debug_log(task, f"📥 任务发起响应: {json.dumps(run_data, ensure_ascii=False)}")
    
    if run_data.get("code") != 0:
        error_msg = f"发起任务失败: {run_data.get('msg', '未知错误')}"
        add_debug_log(task, f"❌ {error_msg}")
        add_debug_log(task, f"完整错误响应: {json.dumps(run_data, ensure_ascii=False)}")
        raise Exception(error_msg)
    
    task_id = run_data['data']['taskId']
    add_debug_log(task, f"✅ 任务创建成功! TaskID: {task_id}")
    return task_id

def fetch_task_output(api_key, task_id, task):
    """获取任务输出"""
    output_url = 'https://www.runninghub.cn/task/openapi/outputs'
    add_debug_log(task, f"📥 获取任务输出: TaskID={task_id}")
    
    response = requests.post(output_url, json={'apiKey': api_key, 'taskId': task_id}, timeout=30)
    response.raise_for_status()
    data = response.json()
    
    add_debug_log(task, f"输出响应: {json.dumps(data, ensure_ascii=False)}")
    
    if data.get("code") == 0 and data.get("data"):
        file_url = data["data"][0].get("fileUrl")
        if file_url:
            add_debug_log(task, f"✅ 获取到结果URL: {file_url}")
            return file_url
        else:
            raise Exception("未找到图片URL")
    else:
        raise Exception("获取结果失败")

def download_result_image(url, task):
    """下载结果图片"""
    add_debug_log(task, f"⬇️ 下载结果图片: {url}")
    response = requests.get(url, stream=True, timeout=60)
    response.raise_for_status()
    content = response.content
    add_debug_log(task, f"✅ 图片下载完成: {len(content)} bytes")
    return content

def process_single_task(task, api_key, webapp_id, node_info):
    """处理单个任务"""
    try:
        add_debug_log(task, "=" * 50)
        add_debug_log(task, f"开始处理任务: {task.file_name}")
        add_debug_log(task, "=" * 50)
        
        task.status = "UPLOADING"
        task.start_time = time.time()
        task.progress = 5
        
        # 步骤1: 上传文件
        add_debug_log(task, "📍 步骤1: 上传文件")
        uploaded_filename = upload_file(task.file_data, task.file_name, api_key, task)
        task.progress = 15
        
        # 步骤2: 准备节点信息 - 使用深拷贝
        add_debug_log(task, "📍 步骤2: 准备节点信息")
        add_debug_log(task, f"原始NODE_INFO: {json.dumps(node_info, ensure_ascii=False)}")
        
        node_info_list = copy.deepcopy(node_info)
        add_debug_log(task, "✅ 已执行深拷贝")
        
        # 更新图片节点
        for node in node_info_list:
            if node["nodeId"] == "38":
                old_value = node["fieldValue"]
                node["fieldValue"] = uploaded_filename
                add_debug_log(task, f"🔄 更新节点38: {old_value} -> {uploaded_filename}")
        
        add_debug_log(task, f"更新后的node_info_list: {json.dumps(node_info_list, ensure_ascii=False)}")
        
        # 验证原始NODE_INFO是否被污染
        original_image_node = next((n for n in node_info if n["nodeId"] == "38"), None)
        if original_image_node:
            add_debug_log(task, f"🔍 验证: 原始NODE_INFO中节点38的值仍为: {original_image_node['fieldValue']}")
        
        # 步骤3: 发起任务
        add_debug_log(task, "📍 步骤3: 发起API任务")
        task.api_task_id = run_task(api_key, webapp_id, node_info_list, task)
        task.status = "PROCESSING"
        task.progress = 20
        
        # 步骤4: 轮询状态
        add_debug_log(task, "📍 步骤4: 轮询任务状态")
        for progress in range(20, 96, 5):
            task.progress = progress
            time.sleep(2)
            
            status_url = 'https://www.runninghub.cn/task/openapi/status'
            response = requests.post(status_url, json={'apiKey': api_key, 'taskId': task.api_task_id}, timeout=10)
            data = response.json()
            status = data.get('data')
            
            add_debug_log(task, f"状态检查: {status} (进度: {progress}%)")
            
            if status == "SUCCESS":
                add_debug_log(task, "✅ 任务处理成功!")
                break
            elif status == "FAILED":
                add_debug_log(task, "❌ 任务处理失败!")
                raise Exception("任务处理失败")
        
        # 步骤5: 获取结果
        add_debug_log(task, "📍 步骤5: 获取处理结果")
        task.progress = 95
        result_url = fetch_task_output(api_key, task.api_task_id, task)
        task.result_url = result_url
        
        # 步骤6: 下载结果
        add_debug_log(task, "📍 步骤6: 下载结果图片")
        task.result_data = download_result_image(result_url, task)
        task.progress = 100
        task.status = "SUCCESS"
        task.elapsed_time = time.time() - task.start_time
        
        add_debug_log(task, "=" * 50)
        add_debug_log(task, f"✅ 任务完成! 总耗时: {task.elapsed_time:.2f}秒")
        add_debug_log(task, "=" * 50)
        
    except Exception as e:
        task.status = "FAILED"
        task.error_message = str(e)
        task.elapsed_time = time.time() - task.start_time if task.start_time else 0
        add_debug_log(task, "=" * 50)
        add_debug_log(task, f"❌ 任务失败: {str(e)}")
        add_debug_log(task, "=" * 50)

# 主界面
st.title("🎨 RunningHub AI - 智能图片优化工具 (调试版)")
st.markdown("### 带详细日志输出，用于排查问题")

# 统计信息
col1, col2, col3, col4 = st.columns(4)
with col1:
    queued = sum(1 for t in st.session_state.tasks if t.status == "QUEUED")
    st.metric("队列中", queued)
with col2:
    processing = sum(1 for t in st.session_state.tasks if t.status in ["UPLOADING", "PROCESSING"])
    st.metric(f"处理中", f"{processing}/{MAX_CONCURRENT}")
with col3:
    completed = sum(1 for t in st.session_state.tasks if t.status == "SUCCESS")
    st.metric("已完成", completed)
with col4:
    failed = sum(1 for t in st.session_state.tasks if t.status == "FAILED")
    st.metric("失败", failed)

st.markdown("---")

# 左右分栏
left_col, right_col = st.columns([2, 3])

with left_col:
    st.markdown("### 📁 图片上传")
    
    uploaded_files = st.file_uploader(
        "选择图片文件（支持多选）",
        type=['png', 'jpg', 'jpeg', 'webp'],
        accept_multiple_files=True,
        help="可以一次选择多张图片进行批量处理"
    )
    
    if uploaded_files:
        st.success(f"已选择 {len(uploaded_files)} 个文件")
        
        if st.button("🚀 添加到处理队列", type="primary"):
            for uploaded_file in uploaded_files:
                st.session_state.task_counter += 1
                task = TaskItem(
                    task_id=st.session_state.task_counter,
                    file_data=uploaded_file.getvalue(),
                    file_name=uploaded_file.name
                )
                st.session_state.tasks.append(task)
            
            st.success(f"已添加 {len(uploaded_files)} 个任务到队列！")
            st.rerun()
    
    st.markdown("---")
    
    with st.expander("⚙️ API 配置信息", expanded=False):
        st.text_input("API Key", value=API_KEY, disabled=True)
        st.text_input("WebApp ID", value=WEBAPP_ID, disabled=True)
        st.markdown("**节点信息配置：**")
        st.json(NODE_INFO)

with right_col:
    st.markdown("### 📊 任务队列")
    
    if not st.session_state.tasks:
        st.info("暂无任务，请上传图片开始处理")
    else:
        current_processing = sum(1 for t in st.session_state.tasks if t.status in ["UPLOADING", "PROCESSING"])
        
        for task in st.session_state.tasks:
            if task.status == "QUEUED" and current_processing < MAX_CONCURRENT:
                thread = threading.Thread(
                    target=process_single_task,
                    args=(task, API_KEY, WEBAPP_ID, NODE_INFO)
                )
                thread.daemon = True
                thread.start()
                current_processing += 1
        
        # 显示所有任务
        for task in reversed(st.session_state.tasks):
            with st.container():
                st.markdown(f'<div class="task-card">', unsafe_allow_html=True)
                
                col_title, col_status = st.columns([3, 1])
                with col_title:
                    st.markdown(f"**📄 {task.file_name}** (Task-{task.task_id})")
                with col_status:
                    if task.status == "SUCCESS":
                        st.markdown('<span class="success-badge">✅ 完成</span>', unsafe_allow_html=True)
                    elif task.status == "FAILED":
                        st.markdown('<span class="error-badge">❌ 失败</span>', unsafe_allow_html=True)
                    elif task.status in ["UPLOADING", "PROCESSING"]:
                        st.markdown('<span class="processing-badge">⚡ 处理中</span>', unsafe_allow_html=True)
                    else:
                        st.markdown('<span class="info-badge">⏸️ 队列中</span>', unsafe_allow_html=True)
                
                # 进度条
                if task.status in ["UPLOADING", "PROCESSING"]:
                    st.progress(task.progress / 100)
                    st.caption(f"进度: {task.progress}%")
                    
                    if task.start_time:
                        elapsed = time.time() - task.start_time
                        remaining = max(0, 150 - elapsed)
                        minutes = int(remaining // 60)
                        seconds = int(remaining % 60)
                        st.caption(f"剩余时间: 约{minutes}分{seconds}秒")
                
                # 🔍 调试日志 - 重点显示
                if task.debug_info:
                    with st.expander(f"🔍 调试日志 (共{len(task.debug_info)}条)", expanded=(task.status == "FAILED")):
                        log_text = "\n".join(task.debug_info)
                        st.code(log_text, language="log")
                
                # 结果显示
                if task.status == "SUCCESS" and task.result_data:
                    elapsed_str = f"{int(task.elapsed_time//60)}分{int(task.elapsed_time%60)}秒"
                    st.success(f"✅ 处理完成！用时: {elapsed_str}")
                    
                    img = Image.open(io.BytesIO(task.result_data))
                    st.image(img, caption="优化后的图片", use_container_width=True)
                    
                    download_filename = f"optimized_{task.file_name}"
                    st.download_button(
                        label="📥 下载优化后的图片",
                        data=task.result_data,
                        file_name=download_filename,
                        mime="image/png",
                        key=f"download_{task.task_id}"
                    )
                
                elif task.status == "FAILED":
                    st.error(f"❌ 处理失败: {task.error_message}")
                
                st.markdown('</div>', unsafe_allow_html=True)
                st.markdown("---")
        
        if st.button("🗑️ 清空所有任务"):
            st.session_state.tasks = []
            st.session_state.processing_count = 0
            st.rerun()

# 页脚
st.markdown("---")
st.markdown("""
<div style='text-align: center; color: #7f8c8d;'>
    <p>🔍 调试版本：会显示详细的API调用日志</p>
    <p>💡 失败时请展开"调试日志"查看详细信息</p>
</div>
""", unsafe_allow_html=True)

# 自动刷新
if any(t.status in ["UPLOADING", "PROCESSING"] for t in st.session_state.tasks):
    time.sleep(2)
    st.rerun()
