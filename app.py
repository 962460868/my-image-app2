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
    page_title="RunningHub AI - 智能图片优化工具",
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
if 'processed_files' not in st.session_state:
    st.session_state.processed_files = set()

# 配置常量
MAX_CONCURRENT = 5  # 修改为5
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

def upload_file(file_data, file_name, api_key):
    """上传文件到服务器"""
    url = 'https://www.runninghub.cn/task/openapi/upload'
    
    files = {'file': (file_name, file_data)}
    data = {'apiKey': api_key, 'fileType': 'image'}
    
    response = requests.post(url, files=files, data=data, timeout=60)
    response.raise_for_status()
    
    response_data = response.json()
    
    if response_data.get("code") == 0:
        uploaded_filename = response_data['data']['fileName']
        return uploaded_filename
    else:
        error_msg = f"图片上传失败: {response_data.get('msg', '未知错误')}"
        raise Exception(error_msg)

def run_task(api_key, webapp_id, node_info_list):
    """发起任务"""
    run_url = 'https://www.runninghub.cn/task/openapi/ai-app/run'
    headers = {'Content-Type': 'application/json'}
    payload = {
        "apiKey": api_key,
        "webappId": webapp_id,
        "nodeInfoList": node_info_list
    }
    
    response = requests.post(run_url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    
    run_data = response.json()
    
    if run_data.get("code") != 0:
        error_msg = f"发起任务失败: {run_data.get('msg', '未知错误')}"
        raise Exception(error_msg)
    
    task_id = run_data['data']['taskId']
    return task_id

def fetch_task_output(api_key, task_id):
    """获取任务输出"""
    output_url = 'https://www.runninghub.cn/task/openapi/outputs'
    
    response = requests.post(output_url, json={'apiKey': api_key, 'taskId': task_id}, timeout=30)
    response.raise_for_status()
    data = response.json()
    
    if data.get("code") == 0 and data.get("data"):
        file_url = data["data"][0].get("fileUrl")
        if file_url:
            return file_url
        else:
            raise Exception("未找到图片URL")
    else:
        raise Exception(f"获取结果失败: {data.get('msg', '未知错误')}")

def download_result_image(url):
    """下载结果图片"""
    response = requests.get(url, stream=True, timeout=60)
    response.raise_for_status()
    content = response.content
    return content

def process_single_task(task, api_key, webapp_id, node_info):
    """处理单个任务"""
    try:
        task.status = "UPLOADING"
        task.start_time = time.time()
        task.progress = 5
        
        # 步骤1: 上传文件
        uploaded_filename = upload_file(task.file_data, task.file_name, api_key)
        task.progress = 15
        
        # 步骤2: 准备节点信息
        node_info_list = copy.deepcopy(node_info)
        
        # 更新图片节点
        for node in node_info_list:
            if node["nodeId"] == "38":
                node["fieldValue"] = uploaded_filename
        
        # 步骤3: 发起任务
        task.api_task_id = run_task(api_key, webapp_id, node_info_list)
        task.status = "PROCESSING"
        task.progress = 20
        
        # 步骤4: 轮询状态
        progress = 20
        max_polls = 60  # 最多轮询60次（约3分钟）
        poll_count = 0
        status = None
        
        while poll_count < max_polls:
            time.sleep(3)  # 每3秒轮询一次
            poll_count += 1
            
            status_url = 'https://www.runninghub.cn/task/openapi/status'
            response = requests.post(status_url, json={'apiKey': api_key, 'taskId': task.api_task_id}, timeout=10)
            response.raise_for_status()
            data = response.json()
            status = data.get('data')
            
            # 缓慢增长进度条：从20%到95%
            if progress < 95:
                progress += min(2, (95 - progress) / 10)  # 越接近95%增长越慢
                progress = int(progress)
            
            task.progress = progress
            
            if status == "SUCCESS":
                break
            elif status == "FAILED":
                raise Exception("任务处理失败")
            elif status in ["QUEUED", "RUNNING"]:
                # 继续等待
                continue
            else:
                continue
        
        # 检查是否超时
        if poll_count >= max_polls:
            raise Exception("任务处理超时")
        
        # 只有在状态为SUCCESS时才获取结果
        if status == "SUCCESS":
            # 步骤5: 获取结果
            task.progress = 95
            result_url = fetch_task_output(api_key, task.api_task_id)
            task.result_url = result_url
            
            # 步骤6: 下载结果
            task.result_data = download_result_image(result_url)
            task.progress = 100
            task.status = "SUCCESS"
            task.elapsed_time = time.time() - task.start_time
        else:
            raise Exception(f"任务未成功完成，最终状态: {status}")
            
    except Exception as e:
        task.status = "FAILED"
        task.error_message = str(e)
        task.elapsed_time = time.time() - task.start_time if task.start_time else 0

# 主界面
st.title("🎨 RunningHub AI - 智能图片优化工具")
st.markdown("### 专业的AI图片优化和增强服务")

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
        help="可以一次选择多张图片进行批量处理，上传后自动加入处理队列"
    )
    
    # 自动加入队列逻辑
    if uploaded_files:
        new_files = []
        for uploaded_file in uploaded_files:
            file_key = f"{uploaded_file.name}_{uploaded_file.size}"
            if file_key not in st.session_state.processed_files:
                st.session_state.processed_files.add(file_key)
                new_files.append(uploaded_file)
        
        if new_files:
            for uploaded_file in new_files:
                st.session_state.task_counter += 1
                task = TaskItem(
                    task_id=st.session_state.task_counter,
                    file_data=uploaded_file.getvalue(),
                    file_name=uploaded_file.name
                )
                st.session_state.tasks.append(task)
            
            st.success(f"已自动添加 {len(new_files)} 个任务到队列！")
            st.rerun()
        
        st.info(f"当前已选择 {len(uploaded_files)} 个文件")
    
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
                        remaining = max(0, 180 - elapsed)  # 预计3分钟
                        minutes = int(remaining // 60)
                        seconds = int(remaining % 60)
                        st.caption(f"剩余时间: 约{minutes}分{seconds}秒")
                
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
            st.session_state.processed_files = set()
            st.rerun()

# 页脚
st.markdown("---")
st.markdown("""
<div style='text-align: center; color: #7f8c8d;'>
    <p>🚀 支持最多5个并发任务同时处理</p>
    <p>📤 上传文件后自动加入处理队列，无需手动操作</p>
</div>
""", unsafe_allow_html=True)

# 自动刷新
if any(t.status in ["UPLOADING", "PROCESSING"] for t in st.session_state.tasks):
    time.sleep(2)
    st.rerun()
