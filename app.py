import streamlit as st
import requests
import time
from datetime import datetime
import threading
import copy
import json
import random
import redis
import logging
import pickle

# --- 1. 页面配置和全局设置 ---

st.set_page_config(
    page_title="RunningHub AI - 智能图片优化工具",
    page_icon="🎨",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 配置日志，减少噪音
logging.getLogger("tornado.access").setLevel(logging.ERROR)
logging.getLogger("tornado.application").setLevel(logging.ERROR)
logging.getLogger("tornado.general").setLevel(logging.ERROR)

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

# 系统配置（优化性能）
MAX_GLOBAL_CONCURRENT = 5  
MAX_LOCAL_CONCURRENT = 3   
MAX_RETRIES = 3            
POLL_INTERVAL = 3          
MAX_POLL_COUNT = 300       
AUTO_REFRESH_INTERVAL = 8  # 增加到8秒，减少刷新频率
DISPLAY_TIMEOUT_MINUTES = 3  
ACTUAL_TIMEOUT_MINUTES = 15  

# Redis键名
GLOBAL_TASK_QUEUE = "runninghub:task_queue"
GLOBAL_PROCESSING_SET = "runninghub:processing_tasks"
SESSION_DATA_PREFIX = "runninghub:session:"

# 并发限制错误关键词
CONCURRENT_LIMIT_ERRORS = [
    "concurrent limit", "too many requests", "rate limit",
    "队列已满", "并发限制", "服务忙碌", "CONCURRENT_LIMIT_EXCEEDED", "TOO_MANY_REQUESTS"
]

# --- 2. 简化CSS样式 ---

st.markdown("""
<style>
    .main { background-color: #f8f9fa; }
    .stButton>button {
        width: 100%; border-radius: 6px; height: 2.5em;
        background-color: #0066cc; color: white; font-weight: 500;
        transition: background-color 0.2s;
    }
    .stButton>button:hover { background-color: #0052a3; }
    .task-card {
        background: white; border-radius: 8px; padding: 1rem; margin: 0.5rem 0;
        box-shadow: 0 1px 3px rgba(0,0,0,0.1); border-left: 4px solid #0066cc;
    }
    .success-badge { color: #28a745; font-weight: 600; }
    .error-badge { color: #dc3545; font-weight: 600; }
    .processing-badge { color: #fd7e14; font-weight: 600; }
    .queued-badge { color: #6f42c1; font-weight: 600; }
    .metric-box {
        background: white; padding: 0.8rem; border-radius: 6px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.1); text-align: center; margin-bottom: 0.3rem;
    }
    .compact-info { font-size: 0.85em; color: #6c757d; margin: 0.2rem 0; }
</style>
""", unsafe_allow_html=True)

# --- 3. Redis连接初始化（优化缓存） ---

@st.cache_resource(ttl=300)  # 5分钟缓存
def init_redis_connection():
    """初始化Redis连接"""
    try:
        r = redis.Redis(
            host=REDIS_HOST, port=REDIS_PORT,
            decode_responses=False, username="default", password=REDIS_PASSWORD,
            socket_timeout=3, socket_connect_timeout=3,
            retry_on_timeout=True, health_check_interval=60
        )
        r.ping()
        return r, None
    except Exception as e:
        return None, f"Redis连接失败: {str(e)}"

r, redis_error = init_redis_connection()

# --- 4. 简化Session State管理 ---

def get_session_key():
    if 'session_id' not in st.session_state:
        st.session_state.session_id = f"s_{int(time.time())}_{random.randint(100, 999)}"
    return st.session_state.session_id

def save_session_data():
    """异步保存会话数据"""
    if not r:
        return
    try:
        session_key = SESSION_DATA_PREFIX + get_session_key()
        session_data = {
            'tasks': [{'task_id': t.task_id, 'file_name': t.file_name, 'session_id': t.session_id,
                      'status': t.status, 'progress': t.progress, 'retry_count': t.retry_count}
                     for t in st.session_state.get('tasks', [])],
            'task_counter': st.session_state.get('task_counter', 0),
            'timestamp': time.time()
        }
        r.setex(session_key, 1800, pickle.dumps(session_data))  # 30分钟过期
    except:
        pass  # 静默处理保存错误

# 初始化Session State
if 'tasks' not in st.session_state:
    st.session_state.tasks = []
if 'task_counter' not in st.session_state:
    st.session_state.task_counter = 0
if 'file_uploader_key' not in st.session_state:
    st.session_state.file_uploader_key = 0
if 'upload_success' not in st.session_state:
    st.session_state.upload_success = False

# --- 5. 精简任务类 ---

class TaskItem:
    def __init__(self, task_id, file_data, file_name, session_id):
        self.task_id = task_id
        self.file_data = file_data
        self.file_name = file_name
        self.session_id = session_id
        self.status = "QUEUED"
        self.progress = 0
        self.result_data = None
        self.error_message = None
        self.api_task_id = None
        self.created_at = datetime.now()
        self.start_time = None
        self.elapsed_time = None
        self.retry_count = 0

    def to_dict(self):
        return {
            'task_id': self.task_id, 'file_name': self.file_name, 'session_id': self.session_id,
            'created_at': self.created_at.isoformat(), 'retry_count': self.retry_count
        }

# --- 6. 核心API函数（无变化但优化错误处理） ---

def is_concurrent_limit_error(error_msg):
    error_lower = error_msg.lower()
    return any(keyword in error_lower for keyword in CONCURRENT_LIMIT_ERRORS)

def upload_file(file_data, file_name, api_key):
    url = 'https://www.runninghub.cn/task/openapi/upload'
    files = {'file': (file_name, file_data)}
    data = {'apiKey': api_key, 'fileType': 'image'}
    response = requests.post(url, files=files, data=data, timeout=60)
    response.raise_for_status()
    result = response.json()
    if result.get("code") == 0:
        return result['data']['fileName']
    else:
        raise Exception(f"上传失败: {result.get('msg', '未知错误')}")

def run_task(api_key, webapp_id, node_info_list):
    url = 'https://www.runninghub.cn/task/openapi/ai-app/run'
    payload = {"apiKey": api_key, "webappId": webapp_id, "nodeInfoList": node_info_list}
    response = requests.post(url, headers={'Content-Type': 'application/json'}, 
                           json=payload, timeout=30)
    response.raise_for_status()
    result = response.json()
    if result.get("code") != 0:
        raise Exception(f"任务发起失败: {result.get('msg', '未知错误')}")
    return result['data']['taskId']

def get_task_status(api_key, task_id):
    url = 'https://www.runninghub.cn/task/openapi/status'
    response = requests.post(url, json={'apiKey': api_key, 'taskId': task_id}, timeout=10)
    response.raise_for_status()
    return response.json().get('data')

def fetch_task_output(api_key, task_id):
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
    response = requests.get(url, stream=True, timeout=60)
    response.raise_for_status()
    return response.content

# --- 7. 优化任务处理逻辑 ---

def process_single_task(task, api_key, webapp_id, node_info):
    task.status = "PROCESSING"
    task.start_time = time.time()
    
    try:
        # 上传文件
        task.progress = 15
        uploaded_filename = upload_file(task.file_data, task.file_name, api_key)
        
        # 准备节点信息
        task.progress = 25
        node_info_list = copy.deepcopy(node_info)
        for node in node_info_list:
            if node["nodeId"] == "38":
                node["fieldValue"] = uploaded_filename
        
        # 发起任务
        task.progress = 35
        task.api_task_id = run_task(api_key, webapp_id, node_info_list)
        
        # 轮询状态
        poll_count = 0
        while poll_count < MAX_POLL_COUNT:
            time.sleep(POLL_INTERVAL)
            poll_count += 1
            
            status = get_task_status(api_key, task.api_task_id)
            task.progress = min(90, 35 + (55 * poll_count / MAX_POLL_COUNT))
            
            if status == "SUCCESS":
                break
            elif status == "FAILED":
                raise Exception("API任务处理失败")
            
            # 每分钟保存一次
            if poll_count % 20 == 0:
                save_session_data()
        
        if poll_count >= MAX_POLL_COUNT:
            raise Exception(f"任务超时 (>{ACTUAL_TIMEOUT_MINUTES}分钟)")
        
        # 获取结果
        task.progress = 95
        result_url = fetch_task_output(api_key, task.api_task_id)
        task.result_data = download_result_image(result_url)
        
        # 完成
        task.progress = 100
        task.status = "SUCCESS"
        task.elapsed_time = time.time() - task.start_time
        save_session_data()
        
    except Exception as e:
        error_msg = str(e)
        task.elapsed_time = time.time() - task.start_time if task.start_time else 0
        
        if (is_concurrent_limit_error(error_msg) and task.retry_count < MAX_RETRIES):
            task.retry_count += 1
            task.status = "QUEUED"
            task.progress = 0
            time.sleep((2 ** task.retry_count) + random.randint(1, 3))
            if r:
                queue_key = GLOBAL_TASK_QUEUE.encode()
                task_data = json.dumps(task.to_dict()).encode()
                r.rpush(queue_key, task_data)
        else:
            task.status = "FAILED"
            task.error_message = error_msg[:100]  # 限制错误信息长度
            
        save_session_data()
    
    finally:
        if r:
            processing_key = GLOBAL_PROCESSING_SET.encode()
            r.srem(processing_key, str(task.task_id))

# --- 8. 优化队列管理 ---

@st.cache_data(ttl=2)  # 2秒缓存
def get_queue_stats():
    if not r:
        return {'queued': 0, 'global_processing': 0, 'local_processing': 0}
    
    try:
        queue_key = GLOBAL_TASK_QUEUE.encode()
        processing_key = GLOBAL_PROCESSING_SET.encode()
        
        queued = r.llen(queue_key)
        global_processing = r.scard(processing_key)
        local_processing = sum(1 for t in st.session_state.tasks if t.status == "PROCESSING")
        
        return {'queued': queued, 'global_processing': global_processing, 'local_processing': local_processing}
    except:
        return {'queued': 0, 'global_processing': 0, 'local_processing': 0}

def start_new_tasks():
    if not r:
        return
    
    try:
        stats = get_queue_stats()
        available_slots = min(
            MAX_GLOBAL_CONCURRENT - stats['global_processing'],
            MAX_LOCAL_CONCURRENT - stats['local_processing']
        )
        
        if available_slots <= 0:
            return
            
        queue_key = GLOBAL_TASK_QUEUE.encode()
        processing_key = GLOBAL_PROCESSING_SET.encode()
        
        for _ in range(available_slots):
            task_data_bytes = r.lpop(queue_key)
            if not task_data_bytes:
                break
                
            task_data = json.loads(task_data_bytes.decode())
            task_id = task_data['task_id']
            
            local_task = next((t for t in st.session_state.tasks if t.task_id == task_id), None)
            
            if local_task and local_task.file_data:
                local_task.retry_count = task_data.get('retry_count', 0)
                r.sadd(processing_key, str(task_id))
                
                thread = threading.Thread(
                    target=process_single_task,
                    args=(local_task, API_KEY, WEBAPP_ID, NODE_INFO)
                )
                thread.daemon = True
                thread.start()
            else:
                r.rpush(queue_key, task_data_bytes)
                
    except:
        pass  # 静默处理启动错误

# --- 9. 优化主界面 ---

def main():
    st.title("🎨 RunningHub AI - 智能图片优化工具")
    st.caption("高效处理 • 快速响应 • 无图片预览版本")
    
    st.info(f"⏱️ 预计处理时间: {DISPLAY_TIMEOUT_MINUTES}分钟 | 🔄 刷新间隔: {AUTO_REFRESH_INTERVAL}秒")
    st.divider()
    
    # 主界面布局
    left_col, right_col = st.columns([1.8, 3.2])
    
    # 左侧：上传和状态
    with left_col:
        st.markdown("### 📁 文件上传")
        
        if st.session_state.upload_success:
            st.success("✅ 文件已添加到处理队列!")
            st.session_state.upload_success = False
        
        uploaded_files = st.file_uploader(
            "选择图片文件",
            type=['png', 'jpg', 'jpeg', 'webp'],
            accept_multiple_files=True,
            help="支持批量上传，自动加入全局队列",
            key=f"uploader_{st.session_state.file_uploader_key}"
        )
        
        if uploaded_files:
            if not r:
                st.error("⚠️ Redis连接失败")
            else:
                with st.spinner(f'添加 {len(uploaded_files)} 个文件...'):
                    new_tasks = []
                    for file in uploaded_files:
                        st.session_state.task_counter += 1
                        task = TaskItem(
                            st.session_state.task_counter, file.getvalue(), 
                            file.name, get_session_key()
                        )
                        st.session_state.tasks.append(task)
                        new_tasks.append(task)
                
                try:
                    queue_key = GLOBAL_TASK_QUEUE.encode()
                    pipe = r.pipeline()
                    for task in new_tasks:
                        task_data = json.dumps(task.to_dict()).encode()
                        pipe.rpush(queue_key, task_data)
                    pipe.execute()
                    
                    save_session_data()
                    st.session_state.upload_success = True
                    st.session_state.file_uploader_key += 1
                    st.rerun()
                    
                except Exception as e:
                    st.error(f"❌ 添加失败: {str(e)[:50]}...")
        
        st.divider()
        
        # 精简状态面板
        with st.expander("📊 系统状态", expanded=True):
            stats = get_queue_stats()
            local_stats = {
                'success': sum(1 for t in st.session_state.tasks if t.status == "SUCCESS"),
                'failed': sum(1 for t in st.session_state.tasks if t.status == "FAILED"),
                'total': len(st.session_state.tasks)
            }
            
            # 3x2网格布局
            c1, c2, c3 = st.columns(3)
            
            with c1:
                st.markdown(f'<div class="metric-box"><h4 style="margin:0;color:#0066cc">{stats["queued"]}</h4><p style="margin:0;font-size:11px">队列</p></div>', unsafe_allow_html=True)
                st.markdown(f'<div class="metric-box"><h4 style="margin:0;color:#28a745">{local_stats["success"]}</h4><p style="margin:0;font-size:11px">完成</p></div>', unsafe_allow_html=True)
            
            with c2:
                st.markdown(f'<div class="metric-box"><h4 style="margin:0;color:#6f42c1">{stats["global_processing"]}/{MAX_GLOBAL_CONCURRENT}</h4><p style="margin:0;font-size:11px">全局</p></div>', unsafe_allow_html=True)
                st.markdown(f'<div class="metric-box"><h4 style="margin:0;color:#dc3545">{local_stats["failed"]}</h4><p style="margin:0;font-size:11px">失败</p></div>', unsafe_allow_html=True)
            
            with c3:
                st.markdown(f'<div class="metric-box"><h4 style="margin:0;color:#fd7e14">{stats["local_processing"]}/{MAX_LOCAL_CONCURRENT}</h4><p style="margin:0;font-size:11px">本页</p></div>', unsafe_allow_html=True)
                st.markdown(f'<div class="metric-box"><h4 style="margin:0;color:#6c757d">{local_stats["total"]}</h4><p style="margin:0;font-size:11px">总数</p></div>', unsafe_allow_html=True)
        
        # 精简系统信息
        with st.expander("⚙️ 系统信息", expanded=False):
            st.text(f"Redis: {'✅连接' if r else '❌断开'}")
            st.text(f"会话: {get_session_key()}")
            st.text(f"配置: {MAX_GLOBAL_CONCURRENT}全局/{MAX_LOCAL_CONCURRENT}本地并发")
    
    # 右侧：任务列表
    with right_col:
        st.markdown("### 📋 任务列表")
        
        if not st.session_state.tasks:
            st.info("💡 暂无任务，请上传文件开始处理")
        else:
            start_new_tasks()
            
            # 显示任务（最新在前，简化显示）
            for task in reversed(st.session_state.tasks):
                with st.container():
                    st.markdown('<div class="task-card">', unsafe_allow_html=True)
                    
                    # 任务头部信息
                    col1, col2 = st.columns([4, 1])
                    
                    with col1:
                        st.markdown(f"**{task.file_name}** `#{task.task_id}`")
                        if task.retry_count > 0:
                            st.markdown(f'<div class="compact-info">🔄 重试 {task.retry_count}/{MAX_RETRIES}</div>', unsafe_allow_html=True)
                    
                    with col2:
                        if task.status == "SUCCESS":
                            st.markdown('<span class="success-badge">✅ 完成</span>', unsafe_allow_html=True)
                        elif task.status == "FAILED":
                            st.markdown('<span class="error-badge">❌ 失败</span>', unsafe_allow_html=True)
                        elif task.status == "PROCESSING":
                            st.markdown('<span class="processing-badge">⚡ 处理中</span>', unsafe_allow_html=True)
                        else:
                            st.markdown('<span class="queued-badge">⏳ 队列中</span>', unsafe_allow_html=True)
                    
                    # 进度和状态信息
                    if task.status == "PROCESSING":
                        st.progress(task.progress / 100, text=f"进度: {int(task.progress)}%")
                        
                        if task.start_time:
                            elapsed = time.time() - task.start_time
                            display_timeout = DISPLAY_TIMEOUT_MINUTES * 60
                            if elapsed < display_timeout:
                                remaining = max(0, display_timeout - elapsed)
                                st.markdown(f'<div class="compact-info">⏱️ 已用时 {int(elapsed//60)}:{int(elapsed%60):02d} | 预计剩余 {int(remaining//60)}:{int(remaining%60):02d}</div>', unsafe_allow_html=True)
                            else:
                                st.markdown(f'<div class="compact-info">⏱️ 已用时 {int(elapsed//60)}:{int(elapsed%60):02d} | 处理中...</div>', unsafe_allow_html=True)
                    
                    elif task.status == "QUEUED":
                        st.markdown('<div class="compact-info">⏳ 等待处理...</div>', unsafe_allow_html=True)
                    
                    # 结果处理（无图片预览）
                    if task.status == "SUCCESS" and task.result_data:
                        elapsed_str = f"{int(task.elapsed_time//60)}:{int(task.elapsed_time%60):02d}"
                        st.success(f"🎉 处理完成! 用时: {elapsed_str}")
                        
                        # 只显示下载按钮
                        file_size = len(task.result_data) / 1024  # KB
                        st.download_button(
                            label=f"📥 下载结果 ({file_size:.1f}KB)",
                            data=task.result_data,
                            file_name=f"optimized_{task.file_name}",
                            mime="image/png",
                            use_container_width=True
                        )
                    
                    elif task.status == "FAILED":
                        st.error(f"💥 处理失败")
                        if task.error_message:
                            st.markdown(f'<div class="compact-info">错误: {task.error_message}</div>', unsafe_allow_html=True)
                    
                    st.markdown('</div>', unsafe_allow_html=True)
            
            st.divider()
            
            # 操作按钮
            col1, col2, col3 = st.columns(3)
            
            with col1:
                if st.button("🗑️ 清空本页"):
                    st.session_state.tasks = []
                    save_session_data()
                    st.rerun()
            
            with col2:
                if r and st.button("🔥 清空全局"):
                    try:
                        r.delete(GLOBAL_TASK_QUEUE.encode(), GLOBAL_PROCESSING_SET.encode())
                        st.session_state.tasks = []
                        save_session_data()
                        st.success("✅ 已清空")
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ 失败: {str(e)[:30]}...")
            
            with col3:
                if st.button("💾 保存数据"):
                    save_session_data()
                    st.success("✅ 已保存")

    # 简化页脚
    st.divider()
    st.markdown("""
    <div style='text-align: center; color: #6c757d; padding: 15px;'>
        <b>🚀 RunningHub AI - 高性能版</b><br>
        <small>无图片预览 • 快速响应 • 专注处理效率</small>
    </div>
    """, unsafe_allow_html=True)

# --- 10. 优化应用入口 ---

if __name__ == "__main__":
    try:
        main()
        
        # 更智能的刷新逻辑
        has_active_tasks = any(t.status in ["PROCESSING", "QUEUED"] for t in st.session_state.tasks)
        
        if has_active_tasks:
            time.sleep(AUTO_REFRESH_INTERVAL)
            st.rerun()
            
    except Exception as e:
        error_str = str(e).lower()
        if not any(kw in error_str for kw in ['websocket', 'tornado', 'streamlit']):
            st.error(f"⚠️ 系统错误: {str(e)[:100]}...")
            st.info("系统将自动恢复...")
            time.sleep(5)
        st.rerun()
