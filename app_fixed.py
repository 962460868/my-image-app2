import streamlit as st
import requests
import time
import io
from PIL import Image
from datetime import datetime
import threading
import base64
import copy  # 导入copy模块用于深拷贝

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
    .upload-box {
        border: 2px dashed #3498db;
        border-radius: 10px;
        padding: 2rem;
        text-align: center;
        background-color: #e8f4f8;
        margin: 1rem 0;
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
    h1 {
        color: #2c3e50;
    }
    h2, h3 {
        color: #2c3e50;
    }
    .stProgress > div > div > div {
        background-color: #3498db;
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
        self.status = "QUEUED"  # QUEUED, UPLOADING, PROCESSING, SUCCESS, FAILED
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
        return response_data['data']['fileName']
    else:
        raise Exception(f"图片上传失败: {response_data.get('msg', '未知错误')}")

def run_task(api_key, webapp_id, node_info_list):
    """发起任务"""
    run_url = 'https://www.runninghub.cn/task/openapi/run'
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
        raise Exception(f"发起任务失败: {run_data.get('msg', '未知错误')}")
    
    return run_data['data']['taskId']

def poll_task_status(api_key, task_id):
    """轮询任务状态"""
    status_url = 'https://www.runninghub.cn/task/openapi/status'
    
    while True:
        response = requests.post(status_url, json={'apiKey': api_key, 'taskId': task_id}, timeout=10)
        response.raise_for_status()
        data = response.json()
        status = data.get('data')
        
        if status == "SUCCESS":
            return "SUCCESS"
        elif status == "FAILED":
            raise Exception("任务处理失败")
        elif status in ["QUEUED", "RUNNING"]:
            time.sleep(3)  # 每3秒轮询一次
        else:
            raise Exception(f"未知状态: {status}")

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
        raise Exception("获取结果失败")

def download_result_image(url):
    """下载结果图片"""
    response = requests.get(url, stream=True, timeout=60)
    response.raise_for_status()
    return response.content

def process_single_task(task, api_key, webapp_id, node_info):
    """处理单个任务"""
    try:
        task.status = "UPLOADING"
        task.start_time = time.time()
        task.progress = 5
        
        # 上传文件
        uploaded_filename = upload_file(task.file_data, task.file_name, api_key)
        task.progress = 15
        
        # ✅ 修复：使用深拷贝避免修改原始NODE_INFO
        node_info_list = copy.deepcopy(node_info)
        for node in node_info_list:
            if node["nodeId"] == "38":
                node["fieldValue"] = uploaded_filename
        
        # 发起任务
        task.api_task_id = run_task(api_key, webapp_id, node_info_list)
        task.status = "PROCESSING"
        task.progress = 20
        
        # 轮询状态
        for progress in range(20, 96, 5):
            task.progress = progress
            time.sleep(2)
            
            # 检查状态
            status_url = 'https://www.runninghub.cn/task/openapi/status'
            response = requests.post(status_url, json={'apiKey': api_key, 'taskId': task.api_task_id}, timeout=10)
            data = response.json()
            status = data.get('data')
            
            if status == "SUCCESS":
                break
            elif status == "FAILED":
                raise Exception("任务处理失败")
        
        # 获取结果
        task.progress = 95
        result_url = fetch_task_output(api_key, task.api_task_id)
        task.result_url = result_url
        
        # 下载结果
        task.result_data = download_result_image(result_url)
        task.progress = 100
        task.status = "SUCCESS"
        task.elapsed_time = time.time() - task.start_time
        
    except Exception as e:
        task.status = "FAILED"
        task.error_message = str(e)
        task.elapsed_time = time.time() - task.start_time if task.start_time else 0
    finally:
        # 处理完成后减少计数
        st.session_state.processing_count = max(0, st.session_state.processing_count - 1)

def get_image_download_link(img_data, filename):
    """生成图片下载链接"""
    b64 = base64.b64encode(img_data).decode()
    href = f'<a href="data:image/png;base64,{b64}" download="{filename}">📥 下载优化后的图片</a>'
    return href

# 主界面
st.title("🎨 RunningHub AI - 智能图片优化工具")
st.markdown("### 支持批量队列处理，最多同时处理3张图片")

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
    
    # 文件上传区域
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
                # 创建新任务
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
    
    # API配置信息（只读显示）
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
        # 处理队列中的任务
        for task in st.session_state.tasks:
            if task.status == "QUEUED" and st.session_state.processing_count < MAX_CONCURRENT:
                st.session_state.processing_count += 1
                # 在新线程中处理任务
                thread = threading.Thread(
                    target=process_single_task,
                    args=(task, API_KEY, WEBAPP_ID, NODE_INFO)
                )
                thread.daemon = True
                thread.start()
        
        # 显示所有任务
        for task in reversed(st.session_state.tasks):
            with st.container():
                st.markdown(f'<div class="task-card">', unsafe_allow_html=True)
                
                # 任务标题
                col_title, col_status = st.columns([3, 1])
                with col_title:
                    st.markdown(f"**📄 {task.file_name}**")
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
                    
                    # 显示预估时间
                    if task.start_time:
                        elapsed = time.time() - task.start_time
                        remaining = max(0, 150 - elapsed)
                        minutes = int(remaining // 60)
                        seconds = int(remaining % 60)
                        st.caption(f"剩余时间: 约{minutes}分{seconds}秒")
                
                # 结果显示
                if task.status == "SUCCESS" and task.result_data:
                    elapsed_str = f"{int(task.elapsed_time//60)}分{int(task.elapsed_time%60)}秒"
                    st.success(f"✅ 处理完成！用时: {elapsed_str}")
                    
                    # 显示图片
                    img = Image.open(io.BytesIO(task.result_data))
                    st.image(img, caption="优化后的图片", use_container_width=True)
                    
                    # 下载按钮
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
        
        # 清空按钮
        if st.button("🗑️ 清空所有任务"):
            st.session_state.tasks = []
            st.session_state.processing_count = 0
            st.rerun()

# 页脚
st.markdown("---")
st.markdown("""
<div style='text-align: center; color: #7f8c8d;'>
    <p>💡 提示：支持同时处理最多3张图片，其余图片将在队列中等待</p>
    <p>⏱️ 每张图片预计处理时间约2-3分钟</p>
</div>
""", unsafe_allow_html=True)

# 自动刷新处理中的任务
if any(t.status in ["UPLOADING", "PROCESSING"] for t in st.session_state.tasks):
    time.sleep(2)
    st.rerun()
