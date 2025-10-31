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
import random
import streamlit.components.v1 as components
import redis
import logging

# --- 1. 页面配置和全局设置 ---

st.set_page_config(
    page_title="RunningHub AI - 智能图片优化工具",
    page_icon="🎨",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 配置日志，减少噪音
logging.getLogger("tornado.access").setLevel(logging.WARNING)
logging.getLogger("tornado.application").setLevel(logging.WARNING)
logging.getLogger("tornado.general").setLevel(logging.WARNING)

# Redis配置
REDIS_HOST = 'redis-18743.c340.ap-northeast-2-1.ec2.redns.redis-cloud.com'
REDIS_PORT = 18743
REDIS_PASSWORD = "dBAPubXYReEwHaIvnvX0lvr3qIgtudCp"

# API配置
API_KEY = "c95f4c4d2703479abfbc55eefeb9bb71"
WEBAPP_ID = "1947599512657453057"
NODE_INFO = [
    {"nodeId": "38", "fieldName": "image", "fieldValue": "placeholder.png", "description": "图片输入"},
    {"nodeId": "60", "fieldName": "text", "fieldValue": "8k, high quality, high detail", "description": "正向提示词补充"},
    {"nodeId": "4", "fieldName": "text", "fieldValue": "色调艳丽,过曝,静态,细节模糊不清,字幕,风格,作品,画作,画面,静止,整体发灰,最差质量,低质量,JPEG压缩残留,丑陋的,残缺的,多余的手指,画得不好的手部,画得不好的脸部,畸形的,毁容的,形态畸形的肢体,手指融合,静止不动的画面,悲乱的背景,三条腿,背景人很多,倒着走", "description": "反向提示词"}
]

# 系统配置（优化稳定性）
MAX_GLOBAL_CONCURRENT = 3  # 降低全局并发数，提高稳定性
MAX_RETRIES = 3            # 降低重试次数，避免无限重试
POLL_INTERVAL = 3          # 轮询间隔
AUTO_REFRESH_INTERVAL = 3  # 页面自动刷新间隔

# Redis键名
GLOBAL_TASK_QUEUE = "runninghub:task_queue"
GLOBAL_PROCESSING_SET = "runninghub:processing_tasks"

# 并发限制错误关键词
CONCURRENT_LIMIT_ERRORS = [
    "concurrent limit", "too many requests", "rate limit",
    "队列已满", "并发限制", "服务忙碌", "CONCURRENT_LIMIT_EXCEEDED", "TOO_MANY_REQUESTS"
]

# --- 2. 自定义CSS样式 ---

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
        transition: all 0.3s ease;
    }
    .stButton>button:hover {
        background-color: #2980b9;
        transform: translateY(-1px);
    }
    .task-card {
        background-color: white;
        border-radius: 10px;
        padding: 1.5rem;
        margin: 1rem 0;
        box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        border: 1px solid #e1e8ed;
    }
    .success-badge { color: #27ae60; font-weight: bold; }
    .error-badge { color: #e74c3c; font-weight: bold; }
    .processing-badge { color: #f39c12; font-weight: bold; }
    .info-badge { color: #17a2b8; font-weight: bold; }
    .waiting-badge { color: #9b59b6; font-weight: bold; }
    .metric-container {
        background: white;
        padding: 1rem;
        border-radius: 8px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
        text-align: center;
        border: 1px solid #e1e8ed;
    }
</style>
""", unsafe_allow_html=True)

# --- 3. Redis连接初始化 ---

@st.cache_resource
def init_redis_connection():
    """初始化Redis连接（缓存资源，避免重复连接）"""
    try:
        r = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            decode_responses=True,
            username="default",
            password=REDIS_PASSWORD,
            socket_timeout=5,
            socket_connect_timeout=5,
            retry_on_timeout=True,
            health_check_interval=30
        )
        r.ping()
        return r, None
    except Exception as e:
        return None, f"Redis连接失败: {str(e)}"

r, redis_error = init_redis_connection()

# --- 4. Session State初始化 ---

if 'tasks' not in st.session_state:
    st.session_state.tasks = []
if 'task_counter' not in st.session_state:
    st.session_state.task_counter = 0
if 'file_uploader_key' not in st.session_state:
    st.session_state.file_uploader_key = 0

# --- 5. 任务类定义（简化版） ---

class TaskItem:
    """简化的任务项类"""
    def __init__(self, task_id, file_data, file_name):
        self.task_id = task_id
        self.file_data = file_data
        self.file_name = file_name
        self.status = "QUEUED"  # QUEUED, PROCESSING, SUCCESS, FAILED
        self.progress = 0
        self.result_data = None
        self.error_message = None
        self.api_task_id = None
        self.created_at = datetime.now()
        self.start_time = None
        self.elapsed_time = None
        self.retry_count = 0

    def to_dict(self):
        """序列化为字典"""
        return {
            'task_id': self.task_id,
            'file_name': self.file_name,
            'created_at': self.created_at.isoformat(),
            'retry_count': self.retry_count
        }

# --- 6. 图片对比组件（简化稳定版） ---

def create_image_comparison(original_data, result_data, task_id):
    """创建简化的图片对比组件"""
    original_b64 = base64.b64encode(original_data).decode()
    result_b64 = base64.b64encode(result_data).decode()
    
    html_code = f"""
    <div id="comparison-container-{task_id}" style="position: relative; width: 100%; max-width: 800px; margin: 0 auto; border-radius: 10px; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.15);">
        <!-- 原图背景 -->
        <img id="original-{task_id}" src="data:image/png;base64,{original_b64}" 
             style="width: 100%; height: auto; display: block;" alt="原图">
        
        <!-- 结果图遮罩 -->
        <div id="result-overlay-{task_id}" style="position: absolute; top: 0; left: 0; width: 70%; height: 100%; overflow: hidden;">
            <img src="data:image/png;base64,{result_b64}" 
                 style="width: 142.86%; height: 100%; object-fit: cover;" alt="AI优化">
        </div>
        
        <!-- 分割线 -->
        <div id="divider-{task_id}" style="position: absolute; top: 0; width: 3px; height: 100%; background: #3498db; cursor: ew-resize; z-index: 10; left: 70%; margin-left: -1.5px; box-shadow: 0 0 8px rgba(52,152,219,0.5);">
            <!-- 拖动手柄 -->
            <div style="position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); width: 32px; height: 32px; background: #3498db; border-radius: 50%; display: flex; align-items: center; justify-content: center; box-shadow: 0 2px 6px rgba(0,0,0,0.2); border: 2px solid white;">
                <span style="color: white; font-size: 12px; font-weight: bold;">⟷</span>
            </div>
        </div>
        
        <!-- 标签 -->
        <div style="position: absolute; top: 10px; left: 10px; background: rgba(52, 152, 219, 0.9); color: white; padding: 4px 8px; border-radius: 12px; font-size: 11px; font-weight: bold;">
            AI优化
        </div>
        <div style="position: absolute; top: 10px; right: 10px; background: rgba(0,0,0,0.7); color: white; padding: 4px 8px; border-radius: 12px; font-size: 11px; font-weight: bold;">
            原图
        </div>
        
        <!-- 下载按钮 -->
        <div id="download-btn-{task_id}" style="position: absolute; bottom: 10px; right: 10px; width: 40px; height: 40px; background: rgba(52, 152, 219, 0.9); border-radius: 50%; display: flex; align-items: center; justify-content: center; cursor: pointer; box-shadow: 0 2px 6px rgba(0,0,0,0.3); transition: all 0.3s ease;" 
             onmouseover="this.style.background='rgba(52, 152, 219, 1)'; this.style.transform='scale(1.1)'"
             onmouseout="this.style.background='rgba(52, 152, 219, 0.9)'; this.style.transform='scale(1)'">
            <span style="color: white; font-size: 18px;">⬇</span>
        </div>
    </div>

    <script>
    (function() {{
        const container = document.getElementById('comparison-container-{task_id}');
        const divider = document.getElementById('divider-{task_id}');
        const resultOverlay = document.getElementById('result-overlay-{task_id}');
        const downloadBtn = document.getElementById('download-btn-{task_id}');
        
        if (!container || !divider || !resultOverlay) return;
        
        let isDragging = false;
        
        function updateComparison(percentage) {{
            percentage = Math.max(10, Math.min(90, percentage));
            divider.style.left = percentage + '%';
            resultOverlay.style.width = percentage + '%';
            const img = resultOverlay.querySelector('img');
            if (img) {{
                img.style.width = (100 / percentage * 100) + '%';
            }}
        }}
        
        function handleDrag(e) {{
            const rect = container.getBoundingClientRect();
            const x = (e.type.includes('touch') ? e.touches[0].clientX : e.clientX) - rect.left;
            const percentage = (x / rect.width) * 100;
            updateComparison(percentage);
        }}
        
        divider.addEventListener('mousedown', function(e) {{
            isDragging = true;
            document.addEventListener('mousemove', handleDrag);
            document.addEventListener('mouseup', function() {{
                isDragging = false;
                document.removeEventListener('mousemove', handleDrag);
            }});
            e.preventDefault();
        }});
        
        container.addEventListener('click', function(e) {{
            if (e.target === downloadBtn || downloadBtn.contains(e.target)) return;
            handleDrag(e);
        }});
        
        // 下载功能
        downloadBtn.addEventListener('click', function(e) {{
            e.stopPropagation();
            const link = document.createElement('a');
            link.href = 'data:image/png;base64,{result_b64}';
            link.download = 'optimized_image.png';
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            
            // 下载反馈
            const original = this.innerHTML;
            this.innerHTML = '<span style="color: white; font-size: 16px;">✓</span>';
            setTimeout(() => {{ this.innerHTML = original; }}, 1500);
        }});
        
        // 初始化
        updateComparison(70);
    }})();
    </script>
    """
    return html_code

# --- 7. 核心API函数 ---

def is_concurrent_limit_error(error_msg):
    """检查是否是并发限制错误"""
    error_lower = error_msg.lower()
    return any(keyword in error_lower for keyword in CONCURRENT_LIMIT_ERRORS)

def upload_file(file_data, file_name, api_key):
    """上传文件"""
    url = 'https://www.runninghub.cn/task/openapi/upload'
    files = {'file': (file_name, file_data)}
    data = {'apiKey': api_key, 'fileType': 'image'}
    
    response = requests.post(url, files=files, data=data, timeout=60)
    response.raise_for_status()
    result = response.json()
    
    if result.get("code") == 0:
        return result['data']['fileName']
    else:
        raise Exception(f"文件上传失败: {result.get('msg', '未知错误')}")

def run_task(api_key, webapp_id, node_info_list):
    """发起任务"""
    url = 'https://www.runninghub.cn/task/openapi/ai-app/run'
    payload = {
        "apiKey": api_key,
        "webappId": webapp_id,
        "nodeInfoList": node_info_list
    }
    
    response = requests.post(url, headers={'Content-Type': 'application/json'}, 
                           json=payload, timeout=30)
    response.raise_for_status()
    result = response.json()
    
    if result.get("code") != 0:
        raise Exception(f"任务发起失败: {result.get('msg', '未知错误')}")
    
    return result['data']['taskId']

def get_task_status(api_key, task_id):
    """获取任务状态"""
    url = 'https://www.runninghub.cn/task/openapi/status'
    response = requests.post(url, json={'apiKey': api_key, 'taskId': task_id}, timeout=10)
    response.raise_for_status()
    return response.json().get('data')

def fetch_task_output(api_key, task_id):
    """获取任务输出"""
    url = 'https://www.runninghub.cn/task/openapi/outputs'
    response = requests.post(url, json={'apiKey': api_key, 'taskId': task_id}, timeout=30)
    response.raise_for_status()
    data = response.json()
    
    if data.get("code") == 0 and data.get("data"):
        file_url = data["data"][0].get("fileUrl")
        if file_url:
            return file_url
    
    raise Exception(f"获取结果失败: {data.get('msg', '未找到结果')}")

def download_result_image(url):
    """下载结果图片"""
    response = requests.get(url, stream=True, timeout=60)
    response.raise_for_status()
    return response.content

# --- 8. 任务处理核心逻辑 ---

def process_single_task(task, api_key, webapp_id, node_info):
    """处理单个任务（简化稳定版）"""
    task.status = "PROCESSING"
    task.start_time = time.time()
    
    try:
        # 步骤1: 上传文件
        task.progress = 10
        uploaded_filename = upload_file(task.file_data, task.file_name, api_key)
        
        # 步骤2: 准备节点信息
        task.progress = 20
        node_info_list = copy.deepcopy(node_info)
        for node in node_info_list:
            if node["nodeId"] == "38":
                node["fieldValue"] = uploaded_filename
        
        # 步骤3: 发起任务
        task.progress = 30
        task.api_task_id = run_task(api_key, webapp_id, node_info_list)
        
        # 步骤4: 轮询状态
        max_polls = 60
        poll_count = 0
        
        while poll_count < max_polls:
            time.sleep(POLL_INTERVAL)
            poll_count += 1
            
            status = get_task_status(api_key, task.api_task_id)
            
            # 更新进度
            task.progress = min(90, 30 + (poll_count * 60 / max_polls))
            
            if status == "SUCCESS":
                break
            elif status == "FAILED":
                raise Exception("API任务处理失败")
        
        if poll_count >= max_polls:
            raise Exception("任务处理超时")
        
        # 步骤5: 获取和下载结果
        task.progress = 95
        result_url = fetch_task_output(api_key, task.api_task_id)
        task.result_data = download_result_image(result_url)
        
        # 完成
        task.progress = 100
        task.status = "SUCCESS"
        task.elapsed_time = time.time() - task.start_time
        
    except Exception as e:
        error_msg = str(e)
        task.elapsed_time = time.time() - task.start_time if task.start_time else 0
        
        # 简化重试逻辑
        if (is_concurrent_limit_error(error_msg) and task.retry_count < MAX_RETRIES):
            task.retry_count += 1
            task.status = "QUEUED"
            task.progress = 0
            # 指数退避等待
            wait_time = (2 ** task.retry_count) + random.randint(1, 3)
            time.sleep(wait_time)
            # 重新加入队列
            if r:
                r.rpush(GLOBAL_TASK_QUEUE, json.dumps(task.to_dict()))
        else:
            task.status = "FAILED"
            task.error_message = error_msg
    
    finally:
        # 从处理集合中移除
        if r:
            r.srem(GLOBAL_PROCESSING_SET, str(task.task_id))

# --- 9. 队列管理函数 ---

def get_queue_stats():
    """获取队列统计信息"""
    if not r:
        return {'queued': 0, 'processing': 0}
    
    try:
        queued = r.llen(GLOBAL_TASK_QUEUE)
        processing = r.scard(GLOBAL_PROCESSING_SET)
        return {'queued': queued, 'processing': processing}
    except:
        return {'queued': 0, 'processing': 0}

def start_new_tasks():
    """启动新任务（全局调度）"""
    if not r:
        return
    
    try:
        stats = get_queue_stats()
        available_slots = MAX_GLOBAL_CONCURRENT - stats['processing']
        
        for _ in range(available_slots):
            task_json = r.lpop(GLOBAL_TASK_QUEUE)
            if not task_json:
                break
                
            task_data = json.loads(task_json)
            task_id = task_data['task_id']
            
            # 查找本地任务
            local_task = next((t for t in st.session_state.tasks if t.task_id == task_id), None)
            
            if local_task and local_task.file_data:
                # 更新重试次数
                local_task.retry_count = task_data.get('retry_count', 0)
                
                # 加入处理集合
                r.sadd(GLOBAL_PROCESSING_SET, str(task_id))
                
                # 启动处理线程
                thread = threading.Thread(
                    target=process_single_task,
                    args=(local_task, API_KEY, WEBAPP_ID, NODE_INFO)
                )
                thread.daemon = True
                thread.start()
    except Exception as e:
        st.error(f"启动任务时出错: {e}")

# --- 10. 主界面 ---

def main():
    st.title("🎨 RunningHub AI - 智能图片优化工具")
    st.markdown("### 稳定高效的分布式AI图片处理平台")
    
    # 状态展示
    col1, col2, col3, col4, col5 = st.columns(5)
    
    stats = get_queue_stats()
    local_stats = {
        'success': sum(1 for t in st.session_state.tasks if t.status == "SUCCESS"),
        'failed': sum(1 for t in st.session_state.tasks if t.status == "FAILED"),
        'processing': sum(1 for t in st.session_state.tasks if t.status == "PROCESSING")
    }
    
    with col1:
        st.markdown(f"""
        <div class="metric-container">
            <h3 style="margin:0; color:#3498db;">{stats['queued']}</h3>
            <p style="margin:0; color:#7f8c8d;">队列中</p>
        </div>
        """, unsafe_allow_html=True)
    
    with col2:
        st.markdown(f"""
        <div class="metric-container">
            <h3 style="margin:0; color:#f39c12;">{stats['processing']}/{MAX_GLOBAL_CONCURRENT}</h3>
            <p style="margin:0; color:#7f8c8d;">处理中</p>
        </div>
        """, unsafe_allow_html=True)
    
    with col3:
        st.markdown(f"""
        <div class="metric-container">
            <h3 style="margin:0; color:#27ae60;">{local_stats['success']}</h3>
            <p style="margin:0; color:#7f8c8d;">已完成</p>
        </div>
        """, unsafe_allow_html=True)
    
    with col4:
        st.markdown(f"""
        <div class="metric-container">
            <h3 style="margin:0; color:#e74c3c;">{local_stats['failed']}</h3>
            <p style="margin:0; color:#7f8c8d;">失败</p>
        </div>
        """, unsafe_allow_html=True)
    
    with col5:
        st.markdown(f"""
        <div class="metric-container">
            <h3 style="margin:0; color:#9b59b6;">{len(st.session_state.tasks)}</h3>
            <p style="margin:0; color:#7f8c8d;">总任务</p>
        </div>
        """, unsafe_allow_html=True)
    
    st.markdown("---")
    
    # 主界面布局
    left_col, right_col = st.columns([2, 3])
    
    # 左侧：上传区域
    with left_col:
        st.markdown("### 📁 图片上传")
        
        uploaded_files = st.file_uploader(
            "选择图片文件（支持多选）",
            type=['png', 'jpg', 'jpeg', 'webp'],
            accept_multiple_files=True,
            help="上传后自动加入全局队列，支持多机协同处理",
            key=f"file_uploader_{st.session_state.file_uploader_key}"
        )
        
        if uploaded_files:
            if not r:
                st.error("⚠️ Redis连接失败，无法使用分布式队列功能")
                st.info("错误详情: " + (redis_error or "未知错误"))
            else:
                # 创建新任务
                new_tasks = []
                for file in uploaded_files:
                    st.session_state.task_counter += 1
                    task = TaskItem(
                        task_id=st.session_state.task_counter,
                        file_data=file.getvalue(),
                        file_name=file.name
                    )
                    st.session_state.tasks.append(task)
                    new_tasks.append(task)
                
                # 批量加入队列
                try:
                    pipe = r.pipeline()
                    for task in new_tasks:
                        pipe.rpush(GLOBAL_TASK_QUEUE, json.dumps(task.to_dict()))
                    pipe.execute()
                    
                    st.success(f"✅ 已添加 {len(uploaded_files)} 个任务到队列！")
                    st.session_state.file_uploader_key += 1
                    time.sleep(0.5)
                    st.rerun()
                    
                except Exception as e:
                    st.error(f"❌ 添加任务失败: {e}")
        
        st.markdown("---")
        
        # 系统信息
        with st.expander("⚙️ 系统配置", expanded=False):
            if r:
                st.success("🟢 Redis: 已连接")
            else:
                st.error(f"🔴 Redis: 连接失败 - {redis_error}")
            
            st.info(f"🔧 全局并发限制: {MAX_GLOBAL_CONCURRENT}")
            st.info(f"🔁 最大重试次数: {MAX_RETRIES}")
            st.info(f"⏱️ 轮询间隔: {POLL_INTERVAL}秒")
            
            st.markdown("**API配置:**")
            st.code(f"API Key: {API_KEY[:20]}...", language="text")
            st.code(f"WebApp ID: {WEBAPP_ID}", language="text")
    
    # 右侧：任务列表
    with right_col:
        st.markdown("### 📊 任务状态")
        
        if not st.session_state.tasks:
            st.info("💡 暂无任务，请上传图片开始处理")
        else:
            # 启动新任务
            start_new_tasks()
            
            # 显示任务
            for task in reversed(st.session_state.tasks):
                with st.container():
                    st.markdown('<div class="task-card">', unsafe_allow_html=True)
                    
                    # 任务头部
                    col_info, col_status = st.columns([3, 1])
                    
                    with col_info:
                        st.markdown(f"**📄 {task.file_name}** (ID: {task.task_id})")
                        if task.retry_count > 0:
                            st.caption(f"🔄 重试 {task.retry_count}/{MAX_RETRIES}")
                    
                    with col_status:
                        if task.status == "SUCCESS":
                            st.markdown('<span class="success-badge">✅ 完成</span>', unsafe_allow_html=True)
                        elif task.status == "FAILED":
                            st.markdown('<span class="error-badge">❌ 失败</span>', unsafe_allow_html=True)
                        elif task.status == "PROCESSING":
                            st.markdown('<span class="processing-badge">⚡ 处理中</span>', unsafe_allow_html=True)
                        else:
                            st.markdown('<span class="info-badge">⏳ 队列中</span>', unsafe_allow_html=True)
                    
                    # 进度显示
                    if task.status == "PROCESSING":
                        st.progress(task.progress / 100)
                        st.caption(f"进度: {int(task.progress)}%")
                        
                        if task.start_time:
                            elapsed = time.time() - task.start_time
                            st.caption(f"已用时: {int(elapsed//60)}分{int(elapsed%60)}秒")
                    
                    # 结果显示
                    if task.status == "SUCCESS" and task.result_data:
                        elapsed_str = f"{int(task.elapsed_time//60)}分{int(task.elapsed_time%60)}秒"
                        st.success(f"🎉 处理成功！用时: {elapsed_str}")
                        
                        st.markdown("**🔍 效果对比** (左侧AI优化，右侧原图)")
                        comparison_html = create_image_comparison(
                            task.file_data, task.result_data, task.task_id
                        )
                        components.html(comparison_html, height=500)
                        
                        st.caption("💡 拖动中间分割线或点击图片任意位置对比效果，点击右下角按钮下载优化图片")
                    
                    elif task.status == "FAILED":
                        st.error(f"💥 处理失败: {task.error_message}")
                    
                    st.markdown('</div>', unsafe_allow_html=True)
                    st.markdown("---")
            
            # 操作按钮
            col_clear_local, col_clear_global = st.columns(2)
            
            with col_clear_local:
                if st.button("🗑️ 清空本地任务", help="只清空本地显示的任务"):
                    st.session_state.tasks = []
                    st.rerun()
            
            with col_clear_global:
                if r and st.button("🔥 清空全局队列", 
                                   help="⚠️ 危险操作：清空所有机器的队列和处理中任务"):
                    try:
                        r.delete(GLOBAL_TASK_QUEUE, GLOBAL_PROCESSING_SET)
                        st.session_state.tasks = []
                        st.success("✅ 已清空全局队列")
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ 清空失败: {e}")

    # 页脚
    st.markdown("---")
    st.markdown("""
    <div style='text-align: center; color: #7f8c8d; padding: 20px;'>
        <h4 style='margin: 10px 0; color: #34495e;'>🚀 RunningHub AI - 企业级分布式图片处理</h4>
        <p><strong>🔒 稳定可靠</strong> | 全局并发控制 + 智能重试机制</p>
        <p><strong>⚡ 高效处理</strong> | 多机协同 + 队列自动调度</p>
        <p><strong>🎨 优质效果</strong> | AI智能优化 + 实时对比预览</p>
        <p><strong>💾 便捷下载</strong> | 一键下载高质量优化结果</p>
    </div>
    """, unsafe_allow_html=True)

# --- 11. 应用入口和自动刷新 ---

if __name__ == "__main__":
    try:
        main()
        
        # 智能自动刷新
        should_refresh = (
            any(t.status == "PROCESSING" for t in st.session_state.tasks) or
            (r and (r.llen(GLOBAL_TASK_QUEUE) > 0 or r.scard(GLOBAL_PROCESSING_SET) > 0))
        )
        
        if should_refresh:
            time.sleep(AUTO_REFRESH_INTERVAL)
            st.rerun()
            
    except Exception as e:
        error_str = str(e).lower()
        if any(keyword in error_str for keyword in ['websocket', 'tornado', 'streamlit']):
            # WebSocket等连接错误，静默处理
            pass
        else:
            st.error(f"⚠️ 系统错误: {e}")
            st.info("页面将自动刷新...")
            time.sleep(5)
            st.rerun()
