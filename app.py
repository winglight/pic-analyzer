from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from functools import wraps
import os
from datetime import datetime, timedelta
import secrets
from dotenv import load_dotenv
import base64
import json
import asyncio
import aiohttp
from multiprocessing import Process, Manager, freeze_support
import uuid
import gc
from models import init_db, TaskDB
from telegram.ext import ApplicationBuilder

load_dotenv()  # Load environment variables

# Ollama 配置
OLLAMA_API_URL = os.getenv('OLLAMA_API_URL')
MODEL = os.getenv('MODEL')
PROMPT_GREEN = os.getenv('PROMPT_GREEN')
PROMPT_RED = os.getenv('PROMPT_RED')
PROMPT_DIP = os.getenv('PROMPT_DIP')

# Cloudflare Workers AI 配置
USE_CLOUDFLARE_AI = os.getenv('USE_CLOUDFLARE_AI', 'false').lower() == 'true'
CLOUDFLARE_ACCOUNT_ID = os.getenv('CLOUDFLARE_ACCOUNT_ID')
CLOUDFLARE_AUTH_TOKEN = os.getenv('CLOUDFLARE_AUTH_TOKEN')
CLOUDFLARE_MODEL = os.getenv('CLOUDFLARE_MODEL', '@cf/meta/llama-3.2-11b-vision-instruct')

# 其他配置
UPLOAD_DIR = os.getenv('UPLOAD_DIR')
VALID_TOKEN = os.getenv('VALID_TOKEN')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
HTTP_PROXY = os.getenv('HTTP_PROXY')
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__)
# 修复CORS配置，使用列表指定来源
CORS(app, resources={r"/*": {
    "origins": ["https://www.tradingview.com"],  # 使用列表形式
    "methods": ["GET", "POST", "OPTIONS"],
    "allow_headers": ["Authorization"]
}})

def require_token(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({'error': 'No token provided'}), 401
        
        token = auth_header.split(' ')[1]
        if token != VALID_TOKEN:
            return jsonify({'error': 'Invalid token'}), 401
            
        return f(*args, **kwargs)
    return decorated_function



manager = None
tasks_status = None

init_db()

async def call_ollama_api(base64_image, prompt):
    """
    Async function to call Ollama API
    """
    try:
        if USE_CLOUDFLARE_AI:
            return await call_cloudflare_ai(base64_image, prompt)
        
        payload = {
            "model": MODEL,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "images": [base64_image]
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                OLLAMA_API_URL,
                json=payload,
                headers={'Content-Type': 'application/json'}
            ) as response:
                content = await response.text()
                return json.loads(json.loads(content)["response"])
    except Exception as e:
        print(f"Error in call_ollama_api: {str(e)}")
        raise

# 添加一个标志来跟踪是否已同意条款
CLOUDFLARE_AGREEMENT_SENT = True

async def call_cloudflare_ai(base64_image, prompt):
    """
    Async function to call Cloudflare Workers AI
    """
    global CLOUDFLARE_AGREEMENT_SENT
    
    try:
        url = f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/run/{CLOUDFLARE_MODEL}"
        
        # 如果尚未同意条款，先发送同意请求
        if not CLOUDFLARE_AGREEMENT_SENT:
            agreement_payload = {
                "prompt": "agree"
            }
            
            headers = {
                "Authorization": f"Bearer {CLOUDFLARE_AUTH_TOKEN}",
                "Content-Type": "application/json"
            }
            
            print("Sending agreement to Cloudflare Workers AI...")
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=agreement_payload, headers=headers) as response:
                    agreement_result = await response.json()
                    # 将Unicode编码转为正常字符再打印
                    pretty_result = json.dumps(agreement_result, indent=2, ensure_ascii=False)[:200]
                    print(f"Agreement response: {pretty_result}...")
                    
                    # 标记为已同意，无论结果如何
                    CLOUDFLARE_AGREEMENT_SENT = True
                    
                    # 如果同意失败，可能需要进一步处理
                    if not agreement_result.get('success', False):
                        print(f"Warning: Agreement may have failed: {json.dumps(agreement_result)}")
                        # 如果消息中包含"Thank you for agreeing"，则认为同意成功
                        if any("Thank you for agreeing" in error.get("message", "") 
                              for error in agreement_result.get("errors", [])):
                            print("Agreement appears to be successful despite error response")
        
        # 修改图像编码方式
        # 确保base64字符串不包含前缀
        if "," in base64_image:
            base64_image = base64_image.split(",", 1)[1]
        
        # 将base64转换为二进制字符串
        image_bytes = base64.b64decode(base64_image)
        image_array = [b for b in image_bytes]  # 转换为0-255的整数数组
        
        # 构建请求负载 - 使用二进制字符串格式
        payload = {
            "prompt": prompt,
            "image": image_array,
            "temperature": 0.7,
            "max_tokens": 1024,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                "type": "object",
                "properties": {
                    "condition1": {
                    "type": "boolean"
                    },
                    "condition2": {
                    "type": "boolean"
                    },
                    "condition3": {
                    "type": "boolean"
                    }
                    }
                },
                "required": [
                    "condition1",
                    "condition2",
                    "condition3"
                ]
                }
        }
        
        headers = {
            "Authorization": f"Bearer {CLOUDFLARE_AUTH_TOKEN}",
            "Content-Type": "application/json"
        }
        
        timeout = aiohttp.ClientTimeout(total=60)  # 设置60秒超时
        connector = aiohttp.TCPConnector(ssl=False)  # 禁用SSL验证以避免某些SSL问题
        
        print(f"Sending request to Cloudflare Workers AI: {url}")
        print(f"Using direct image_data format")
        
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            async with session.post(url, json=payload, headers=headers) as response:
                result = await response.json()
                
                # 将Unicode编码转为正常字符再打印
                pretty_result = json.dumps(result, indent=2, ensure_ascii=False)[:500]
                print(f"Cloudflare API response: {pretty_result}...")
                
                if 'result' not in result or not result.get('success', False):
                    # 尝试另一种格式 - 这里保留作为备用，但修改了图片格式
                    print("First attempt failed, trying alternative format...")
                    
                    # 尝试另一种格式
                    alt_payload = {
                        "prompt": prompt,
                        "image": f"data:image/png;base64,{base64_image}"
                    }
                    
                    async with session.post(url, json=alt_payload, headers=headers) as alt_response:
                        alt_result = await alt_response.json()
                        # 将Unicode编码转为正常字符再打印
                        pretty_alt_result = json.dumps(alt_result, indent=2, ensure_ascii=False)[:500]
                        print(f"Alternative format response: {pretty_alt_result}...")
                        
                        if 'result' not in alt_result or not alt_result.get('success', False):
                            raise Exception(f"Cloudflare API error: {json.dumps(result, ensure_ascii=False)}")
                        result = alt_result
                
                # 尝试从响应中提取JSON
                response_text = result['result'].get('response', result['result'])
                try:
                    # 尝试直接解析为JSON
                    if isinstance(response_text, dict):
                        return response_text
                    else:
                        return json.loads(response_text)
                except json.JSONDecodeError:
                    # 如果不是直接的JSON，尝试从文本中提取JSON部分
                    import re
                    json_match = re.search(r'```json\s*(.*?)\s*```', response_text, re.DOTALL)
                    if json_match:
                        return json.loads(json_match.group(1))
                    else:
                        # 尝试提取任何看起来像JSON的部分
                        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                        if json_match:
                            try:
                                return json.loads(json_match.group(0))
                            except:
                                pass
                        
                        # 如果无法提取JSON，返回一个错误对象
                        return {
                            "error": "无法从响应中提取JSON",
                            "raw_response": response_text
                        }
    except Exception as e:
        print(f"Error in call_cloudflare_ai: {str(e)}")
        raise

async def process_image(base64_image):
    """
    Process image with green, red and dip prompts
    """
    tasks = [
        call_ollama_api(base64_image, PROMPT_GREEN),
        call_ollama_api(base64_image, PROMPT_RED),
        call_ollama_api(base64_image, PROMPT_DIP)
    ]
    green_result, red_result, dip_result = await asyncio.gather(*tasks)
    return {
        'green': green_result, 
        'red': red_result,
        'dip': dip_result
    }

async def send_telegram_message(result, file_path):
    """Send analysis results to Telegram if conditions are met"""
    try:
        # Parse results
        green_result = result['green']
        red_result = result['red']
        dip_result = result['dip']
        
        # Check conditions for each type
        conditions = {
            'green_condition1': green_result.get('condition1', False),
            'green_condition2': green_result.get('condition2', False),
            'green_condition3': green_result.get('condition3', False),
            'dip_condition1': dip_result.get('condition1', False),
            'dip_condition2': dip_result.get('condition2', False),
            'dip_condition3': dip_result.get('condition3', False)
        }
        
        # Count true conditions for each type
        green_conditions = sum(1 for k, v in conditions.items() if k.startswith('green_') and v)
        dip_conditions = sum(1 for k, v in conditions.items() if k.startswith('dip_') and v)
        
        # Send message if either green or dip conditions are met
        if green_conditions >= 2 or dip_conditions >= 2:
            app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).proxy(HTTP_PROXY if HTTP_PROXY else None).build()
            
            # Prepare message text
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            message = f"🔄 New Analysis Result ({timestamp})\n\n"
            
            # Add Green conditions if met
            if green_conditions >= 2:
                message += "✅ Green Conditions Met:\n"
                for k, v in conditions.items():
                    if k.startswith('green_'):
                        emoji = "✓" if v else "✗"
                        message += f"{emoji} {k.replace('green_', '')}\n"
                message += f"\n📊 Green Analysis:\n{json.dumps(green_result, indent=2)}\n\n"
            
            # Add Dip conditions if met
            if dip_conditions >= 2:
                message += "📉 Dip Conditions Met:\n"
                for k, v in conditions.items():
                    if k.startswith('dip_'):
                        emoji = "✓" if v else "✗"
                        message += f"{emoji} {k.replace('dip_', '')}\n"
                message += f"\n📊 Dip Analysis:\n{json.dumps(dip_result, indent=2)}\n\n"
            
            message += f"🖼 Image: {os.path.basename(file_path)}"
            
            # Send message and image with timeout
            async with app:
                await app.bot.send_message(
                    chat_id=TELEGRAM_CHAT_ID, 
                    text=message
                )
                with open(file_path, 'rb') as photo:
                    await app.bot.send_photo(
                        chat_id=TELEGRAM_CHAT_ID, 
                        photo=photo
                    )
            return True
        else:
            print(f"Message not sent: Green conditions: {green_conditions}, Dip conditions: {dip_conditions}")
            return False
            
    except Exception as e:
        print(f"Failed to send Telegram message: {str(e)}")
        return False

def cleanup_old_tasks():
    """清理超过30分钟的旧任务"""
    current_time = datetime.now()
    expired_tasks = [
        task_id for task_id, task in tasks_status.items()
        if current_time - datetime.fromisoformat(task['created_at']) > timedelta(minutes=30)
    ]
    for task_id in expired_tasks:
        if task_id in tasks_status:
            del tasks_status[task_id]

# Modify run_async_task function to save telegram sending status
def run_async_task(task_id, base64_image, file_path):
    """在新进程中运行异步任务"""
    # 创建新的事件循环
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        # 使用 run_until_complete 运行协程
        result = loop.run_until_complete(process_image(base64_image))
        
        # 发送 Telegram 消息
        telegram_sent = loop.run_until_complete(send_telegram_message(result, file_path))
        
        # 更新任务状态
        TaskDB.update_task(task_id, 'completed', 
                          result=result, 
                          telegram_sent=telegram_sent)
    except Exception as e:
        print(f"Error in run_async_task: {str(e)}")
        TaskDB.update_task(task_id, 'failed', 
                          error=str(e),
                          telegram_sent=False)
    finally:
        # 确保所有待处理的任务都已完成
        pending = asyncio.all_tasks(loop)
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        
        # 关闭事件循环
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()
        gc.collect()

@app.route('/task/<task_id>/resend', methods=['POST'])
@require_token
def resend_telegram(task_id):
    """Resend telegram message for a specific task"""
    try:
        task = TaskDB.get_task(task_id)
        if not task:
            return jsonify({'error': 'Task not found'}), 404
            
        if task['status'] != 'completed':
            return jsonify({'error': 'Cannot resend message for incomplete task'}), 400

        # 创建新事件循环并运行异步函数
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # 重新发送 Telegram 消息
            telegram_sent = loop.run_until_complete(send_telegram_message(task['result'], task['file_path']))
            
            # 更新任务状态
            TaskDB.update_task(task_id, 'completed', 
                             result=task['result'], 
                             telegram_sent=telegram_sent)
            
            return jsonify({
                'success': telegram_sent,
                'message': 'Telegram message resent successfully' if telegram_sent 
                          else 'Failed to resend telegram message'
            })
        finally:
            # 确保所有待处理的任务都已完成
            pending = asyncio.all_tasks(loop)
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            
            # 关闭事件循环
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    
@app.route('/upload', methods=['OPTIONS'])
def handle_options_request():
    """Handle OPTIONS request with CORS headers"""
    response = jsonify({'message': 'Preflight request'})
    response.headers.add('Access-Control-Allow-Origin', 'https://www.tradingview.com')
    response.headers.add('Access-Control-Allow-Headers', 'Authorization')
    return response, 200

@app.route('/upload', methods=['POST'])
@require_token
def upload_screenshot():

    try:
        # Validate token
        # auth_header = request.headers.get('Authorization')
        # if not auth_header or not auth_header.startswith('Bearer '):
        #     return jsonify({'error': 'No token provided'}), 401
        
        # token = auth_header.split(' ')[1]
        # if token != VALID_TOKEN:
        #     return jsonify({'error': 'Invalid token'}), 401

        # Validate file
        if 'screenshot' not in request.files:
            return jsonify({'error': 'No file provided'}), 400

        file = request.files['screenshot']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400

        # Save file
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        random_hex = secrets.token_hex(4)
        filename = f'screenshot_{timestamp}_{random_hex}.png'
        file_path = os.path.join(UPLOAD_DIR, filename)
        file.save(file_path)

        # Read and encode file
        with open(file_path, 'rb') as f:
            base64_image = base64.b64encode(f.read()).decode('utf-8')

        # Save task to database
        task_id = str(uuid.uuid4())
        TaskDB.create_task(task_id, file_path)

        # Start processing in new process
        cleanup_old_tasks()  # 清理旧任务
        process = Process(target=run_async_task, args=(task_id, base64_image, file_path))
        process.daemon = True  # 设置为守护进程
        process.start()

        # Return immediate response with task ID
        return jsonify({
            'task_id': task_id,
            'status': 'processing',
            'message': 'Image processing started'
        })

    except Exception as e:
        response = jsonify({'error': str(e)})
        response.headers.add('Access-Control-Allow-Origin', 'https://www.tradingview.com')
        return response, 500

@app.route('/task/<task_id>', methods=['GET'])
def get_task(task_id):
    task = TaskDB.get_task(task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404
    return jsonify(task)

@app.route('/tasks', methods=['GET'])
def list_tasks():
    """List tasks with pagination and status filter"""
    try:
        status = request.args.get('status')
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 10))

        tasks, total = TaskDB.get_tasks(status, page, per_page)
        
        return jsonify({
            'tasks': tasks,
            'total': total,
            'page': page,
            'per_page': per_page,
            'total_pages': (total + per_page - 1) // per_page
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/image/<path:file_path>', methods=['GET'])
def get_image(file_path):
    """Return image file by file path"""
    try:
        full_path = os.path.join(UPLOAD_DIR, os.path.basename(file_path))
        if not os.path.exists(full_path):
            return jsonify({'error': 'Image not found'}), 404
        return send_file(full_path, mimetype='image/png')
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    freeze_support()
    manager = Manager()
    tasks_status = manager.dict()
    app.run(host='0.0.0.0', port=5005, debug=True)