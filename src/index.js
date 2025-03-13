/**
 * pic-analyzer Cloudflare Worker
 * 将 Flask 应用转换为 Cloudflare Worker
 */

/**
 * @typedef {Object} Env
 * @property {KVNamespace} pic_analyzer - KV 存储命名空间
 * @property {string} VALID_TOKEN - 验证令牌
 * @property {string} CLOUDFLARE_ACCOUNT_ID - Cloudflare 账户 ID
 * @property {string} CLOUDFLARE_AUTH_TOKEN - Cloudflare 认证令牌
 * @property {string} CLOUDFLARE_MODEL - Cloudflare AI 模型名称
 * @property {string} TELEGRAM_BOT_TOKEN - Telegram 机器人令牌
 * @property {string} TELEGRAM_CHAT_ID - Telegram 聊天 ID
 */

// 分析提示词
const PROMPT_GREEN = `分析附件图片,仅仅考虑蜡烛图中最靠右的11个bar的范围内,是否满足以下三个条件：
 1. 是否出现从上到下垂直的绿色柱子(不同于OCHL bar)
 2. 判断柱子右侧是否出现了小一些的"Buy"绿色标记
 3. 判断柱子右侧是否出现了更大一些的"Buy"绿色标记

 返回结果为json格式，如下：
 {
    "condition1": true, 
    "condition2": true, 
    "condition3": true, 
}`;

const PROMPT_RED = `分析附件图片,仅仅考虑蜡烛图中最靠右的11个bar的范围内,是否满足以下三个条件：
 1. 是否出现从上到下垂直的红色柱子(不同于OCHL bar)
 2. 判断柱子右侧是否出现了小一些的"Sell"红色标记
 3. 判断柱子右侧是否出现了更大一些的"Sell"红色标记

 返回结果为json格式，如下：
 {
    "condition1": true, 
    "condition2": true, 
    "condition3": true, 
}`;

const PROMPT_DIP = `分析附件图片,仅仅考虑蜡烛图中最靠右的11个bar的范围内,是否满足以下三个条件：
 1. 是否出现远超前面高度的红色柱子
 2. 判断是否连续出现远三个远超前面高度的红色柱子
 3. 判断最右侧是否出现了较高的绿色柱子，且Vol值较高

 返回结果为json格式，如下：
 {
    "condition1": true, 
    "condition2": true, 
    "condition3": true, 
}`;

// KV 存储用于任务状态
// 需要在 wrangler.toml 中配置 KV 命名空间
// [[kv_namespaces]]
// binding = "pic_analyzer"
// id = "your-kv-id"

/**
 * 验证请求中的 token
 */
function validateToken(request, env) {
  const authHeader = request.headers.get('Authorization');
  if (!authHeader || !authHeader.startsWith('Bearer ')) {
    return { valid: false, error: 'No token provided' };
  }
  
  const token = authHeader.split(' ')[1];
  if (token !== env.VALID_TOKEN) {
    return { valid: false, error: 'Invalid token' };
  }
  
  return { valid: true };
}

/**
 * 调用 Cloudflare AI 进行图像分析
 */
async function callCloudflareAI(imageData, prompt, env) {
  try {
    const url = `https://api.cloudflare.com/client/v4/accounts/${env.CLOUDFLARE_ACCOUNT_ID}/ai/run/${env.CLOUDFLARE_MODEL}`;
    
    // 确保 base64 字符串不包含前缀
    if (imageData.includes(',')) {
      imageData = imageData.split(',', 2)[1];
    }
    
    // 将 base64 转换为二进制数组
    const binaryString = atob(imageData);
    const bytes = new Uint8Array(binaryString.length);
    for (let i = 0; i < binaryString.length; i++) {
      bytes[i] = binaryString.charCodeAt(i);
    }
    
    // 构建请求负载
    const payload = {
      prompt: prompt,
      image: Array.from(bytes), // 使用整数数组格式
      temperature: 0.7,
      max_tokens: 1024,
      response_format: {
        type: "json_schema",
        json_schema: {
          type: "object",
          properties: {
            condition1: {
              type: "boolean"
            },
            condition2: {
              type: "boolean"
            },
            condition3: {
              type: "boolean"
            }
          },
          required: [
            "condition1",
            "condition2",
            "condition3"
          ]
        }
      }
    };
    
    const headers = {
      'Authorization': `Bearer ${env.CLOUDFLARE_AUTH_TOKEN}`,
      'Content-Type': 'application/json'
    };
    
    console.log(`Sending request to Cloudflare AI: ${url}`);
    
    // 发送请求
    let response = await fetch(url, {
      method: 'POST',
      headers: headers,
      body: JSON.stringify(payload)
    });
    
    let result = await response.json();
    
    // 如果第一次尝试失败，使用 data URI 格式再试一次
    if (!result.success || !result.result) {
      console.log("First attempt failed, trying with data URI format");
      
      const altPayload = {
        prompt: prompt,
        image: `data:image/png;base64,${imageData}`
      };
      
      response = await fetch(url, {
        method: 'POST',
        headers: headers,
        body: JSON.stringify(altPayload)
      });
      
      result = await response.json();
      
      if (!result.success || !result.result) {
        throw new Error(`Cloudflare AI API error: ${JSON.stringify(result)}`);
      }
    }
    
    // 解析响应
    const responseText = result.result.response || result.result;
    
    // 尝试解析 JSON
    if (typeof responseText === 'object') {
      return responseText;
    } else {
      try {
        return JSON.parse(responseText);
      } catch (e) {
        // 尝试从文本中提取 JSON
        const jsonMatch = responseText.match(/\{.*\}/s);
        if (jsonMatch) {
          try {
            return JSON.parse(jsonMatch[0]);
          } catch (e) {
            // 忽略错误
          }
        }
        
        return {
          error: "无法从响应中提取 JSON",
          raw_response: responseText
        };
      }
    }
  } catch (error) {
    console.error(`Error in callCloudflareAI: ${error.message}`);
    throw error;
  }
}

/**
 * 处理图像分析
 */
async function processImage(imageData, env) {
  try {
    // 并行调用三个分析
    const [greenResult, redResult, dipResult] = await Promise.all([
      callCloudflareAI(imageData, PROMPT_GREEN, env),
      callCloudflareAI(imageData, PROMPT_RED, env),
      callCloudflareAI(imageData, PROMPT_DIP, env)
    ]);
    
    return {
      green: greenResult,
      red: redResult,
      dip: dipResult
    };
  } catch (error) {
    console.error(`Error in processImage: ${error.message}`);
    throw error;
  }
}

/**
 * 发送 Telegram 消息
 */
async function sendTelegramMessage(result, imageData, env) {
  try {
    // 检查条件
    const conditions = {};
    let green_conditions = 0;
    let dip_conditions = 0;
    
    // 检查绿色条件
    if (result.green && typeof result.green === 'object') {
      conditions.green_condition1 = result.green.condition1 === true;
      conditions.green_condition2 = result.green.condition2 === true;
      conditions.green_condition3 = result.green.condition3 === true;
      
      green_conditions = Object.values(conditions)
        .filter(v => v === true && Object.keys(conditions).find(k => conditions[k] === v).startsWith('green_'))
        .length;
    }
    
    // 检查下跌条件
    if (result.dip && typeof result.dip === 'object') {
      conditions.dip_condition1 = result.dip.condition1 === true;
      conditions.dip_condition2 = result.dip.condition2 === true;
      conditions.dip_condition3 = result.dip.condition3 === true;
      
      dip_conditions = Object.values(conditions)
        .filter(v => v === true && Object.keys(conditions).find(k => conditions[k] === v).startsWith('dip_'))
        .length;
    }
    
    // 如果条件不满足，不发送消息
    if (green_conditions < 2 && dip_conditions < 2) {
      console.log(`Message not sent: Green conditions: ${green_conditions}, Dip conditions: ${dip_conditions}`);
      return false;
    }
    
    // 准备消息文本
    const timestamp = new Date().toISOString().replace('T', ' ').substring(0, 19);
    let message = `🔄 New Analysis Result (${timestamp})\n\n`;
    
    // 添加绿色条件
    if (green_conditions >= 2) {
      message += "✅ Green Conditions Met:\n";
      for (const [k, v] of Object.entries(conditions)) {
        if (k.startsWith('green_')) {
          const emoji = v ? "✓" : "✗";
          message += `${emoji} ${k.replace('green_', '')}\n`;
        }
      }
      message += `\n📊 Green Analysis:\n${JSON.stringify(result.green, null, 2)}\n\n`;
    }
    
    // 添加下跌条件
    if (dip_conditions >= 2) {
      message += "📉 Dip Conditions Met:\n";
      for (const [k, v] of Object.entries(conditions)) {
        if (k.startsWith('dip_')) {
          const emoji = v ? "✓" : "✗";
          message += `${emoji} ${k.replace('dip_', '')}\n`;
        }
      }
      message += `\n📊 Dip Analysis:\n${JSON.stringify(result.dip, null, 2)}\n\n`;
    }
    
    message += `🖼 Image: Screenshot ${timestamp}`;
    
    // 发送消息
    const telegramUrl = `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`;
    const telegramResponse = await fetch(telegramUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        chat_id: env.TELEGRAM_CHAT_ID,
        text: message
      })
    });
    
    if (!telegramResponse.ok) {
      throw new Error(`Failed to send Telegram message: ${await telegramResponse.text()}`);
    }
    
    // 发送图片
    const photoUrl = `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendPhoto`;
    const formData = new FormData();
    formData.append('chat_id', env.TELEGRAM_CHAT_ID);
    
    // 将 base64 转换为 Blob
    const byteCharacters = atob(imageData.includes(',') ? imageData.split(',', 2)[1] : imageData);
    const byteArrays = [];
    for (let i = 0; i < byteCharacters.length; i++) {
      byteArrays.push(byteCharacters.charCodeAt(i));
    }
    const blob = new Blob([new Uint8Array(byteArrays)], { type: 'image/png' });
    formData.append('photo', blob, 'screenshot.png');
    
    const photoResponse = await fetch(photoUrl, {
      method: 'POST',
      body: formData
    });
    
    if (!photoResponse.ok) {
      throw new Error(`Failed to send Telegram photo: ${await photoResponse.text()}`);
    }
    
    return true;
  } catch (error) {
    console.error(`Failed to send Telegram message: ${error.message}`);
    return false;
  }
}

/**
 * 处理上传请求
 */
async function handleUpload(request, env) {
  try {
    // 验证 token
    const tokenValidation = validateToken(request, env);
    if (!tokenValidation.valid) {
      return new Response(JSON.stringify({ error: tokenValidation.error }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' }
      });
    }
    
    // 解析表单数据
    const formData = await request.formData();
    const file = formData.get('screenshot');
    
    if (!file) {
      return new Response(JSON.stringify({ error: 'No file provided' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' }
      });
    }
    
    // 读取文件内容
    const arrayBuffer = await file.arrayBuffer();
    const base64Image = btoa(String.fromCharCode(...new Uint8Array(arrayBuffer)));
    
    // 创建任务 ID
    const taskId = crypto.randomUUID();
    
    // 存储任务状态
    await env.pic_analyzer.put(`task:${taskId}`, JSON.stringify({
        status: 'processing',
        file_name: file.name,
        created_at: new Date().toISOString(),
        telegram_sent: false
      }), {
        expirationTtl: 60 * 60 * 24 // 24小时过期
      });
      
      // 创建一个新的 Worker 执行环境来处理分析任务
      // 注意：在实际的 Cloudflare Worker 中，我们需要使用 Durable Objects 或 Queues 来处理后台任务
      // 这里简化为直接处理
      env.pic_analyzer.put(`task:${taskId}:image`, base64Image, {
        expirationTtl: 60 * 60 * 24 // 24小时过期
      });
      
      // 启动异步处理
      // 注意：在实际的 Cloudflare Worker 中，这应该通过 Scheduled Worker 或 Queue 来处理
      // 这里我们使用一个异步函数，但不等待它完成
      // (async () => {
        try {
          // 处理图像
          const result = await processImage(base64Image, env);
          
          // 发送 Telegram 消息
          const telegramSent = await sendTelegramMessage(result, base64Image, env);
          
          // 更新任务状态
          await env.pic_analyzer.put(`task:${taskId}`, JSON.stringify({
            status: 'completed',
            file_name: file.name,
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
            result: result,
            telegram_sent: telegramSent
          }), {
            expirationTtl: 60 * 60 * 24 // 24小时过期
          });
        } catch (error) {
          console.error(`Error processing task ${taskId}: ${error.message}`);
          
          // 更新任务状态为失败
          await env.pic_analyzer.put(`task:${taskId}`, JSON.stringify({
            status: 'failed',
            file_name: file.name,
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
            error: error.message,
            telegram_sent: false
          }), {
            expirationTtl: 60 * 60 * 24 // 24小时过期
          });
        }
      // })();
      
      // 立即返回响应，不等待处理完成
      return new Response(JSON.stringify({
        task_id: taskId,
        status: 'processing',
        message: 'Image processing started'
      }), {
        status: 200,
        headers: {
          'Content-Type': 'application/json',
          'Access-Control-Allow-Origin': 'https://www.tradingview.com',
          'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
          'Access-Control-Allow-Headers': 'Authorization'
        }
      });
    } catch (error) {
      console.error(`Error in handleUpload: ${error.message}`);
      return new Response(JSON.stringify({ error: error.message }), {
        status: 500,
        headers: { 'Content-Type': 'application/json' }
      });
    }
  }
  
  /**
   * 获取任务状态
   */
  async function getTask(taskId, env) {
    try {
      const taskData = await env.pic_analyzer.get(`task:${taskId}`, { type: 'json' });
      
      if (!taskData) {
        return new Response(JSON.stringify({ error: 'Task not found' }), {
          status: 404,
          headers: { 'Content-Type': 'application/json' }
        });
      }
      
      return new Response(JSON.stringify(taskData), {
        status: 200,
        headers: { 'Content-Type': 'application/json' }
      });
    } catch (error) {
      console.error(`Error in getTask: ${error.message}`);
      return new Response(JSON.stringify({ error: error.message }), {
        status: 500,
        headers: { 'Content-Type': 'application/json' }
      });
    }
  }
  
  /**
   * 重新发送 Telegram 消息
   */
  async function resendTelegram(taskId, env) {
    try {
      // 验证 token
      const tokenValidation = validateToken(request, env);
      if (!tokenValidation.valid) {
        return new Response(JSON.stringify({ error: tokenValidation.error }), {
          status: 401,
          headers: { 'Content-Type': 'application/json' }
        });
      }
      
      const taskData = await env.pic_analyzer.get(`task:${taskId}`, { type: 'json' });
      
      if (!taskData) {
        return new Response(JSON.stringify({ error: 'Task not found' }), {
          status: 404,
          headers: { 'Content-Type': 'application/json' }
        });
      }
      
      if (taskData.status !== 'completed') {
        return new Response(JSON.stringify({ error: 'Cannot resend message for incomplete task' }), {
          status: 400,
          headers: { 'Content-Type': 'application/json' }
        });
      }
      
      // 获取图像数据
      const imageData = await env.pic_analyzer.get(`task:${taskId}:image`);
      
      if (!imageData) {
        return new Response(JSON.stringify({ error: 'Image data not found' }), {
          status: 404,
          headers: { 'Content-Type': 'application/json' }
        });
      }
      
      // 重新发送 Telegram 消息
      const telegramSent = await sendTelegramMessage(taskData.result, imageData, env);
      
      // 更新任务状态
      await env.pic_analyzer.put(`task:${taskId}`, JSON.stringify({
        ...taskData,
        updated_at: new Date().toISOString(),
        telegram_sent: telegramSent
      }), {
        expirationTtl: 60 * 60 * 24 // 24小时过期
      });
      
      return new Response(JSON.stringify({
        success: telegramSent,
        message: telegramSent ? 'Telegram message resent successfully' : 'Failed to resend telegram message'
      }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' }
      });
    } catch (error) {
      console.error(`Error in resendTelegram: ${error.message}`);
      return new Response(JSON.stringify({ error: error.message }), {
        status: 500,
        headers: { 'Content-Type': 'application/json' }
      });
    }
  }
  
  /**
   * 处理 OPTIONS 请求（CORS 预检）
   */
  function handleOptions() {
    return new Response(null, {
      status: 204,
      headers: {
        'Access-Control-Allow-Origin': 'https://www.tradingview.com',
        'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Authorization, Content-Type',
        'Access-Control-Max-Age': '86400'
      }
    });
  }
  
  /**
   * 主处理函数
   */
  export default {
    async fetch(request, env, ctx) {
      // 处理 CORS 预检请求
      if (request.method === 'OPTIONS') {
        return handleOptions();
      }
      
      const url = new URL(request.url);
      const path = url.pathname;
      
      // 路由处理
      if (path === '/upload' && request.method === 'POST') {
        return await handleUpload(request, env);
      } else if (path.startsWith('/task/') && path.endsWith('/resend') && request.method === 'POST') {
        const taskId = path.split('/')[2];
        return await resendTelegram(request, taskId, env);
      } else if (path.startsWith('/task/') && request.method === 'GET') {
        const taskId = path.split('/')[2];
        return await getTask(taskId, env);
      } else if (path === '/tasks' && request.method === 'GET') {
        return await getTasks(request, env);
      } else if (path.startsWith('/image/') && request.method === 'GET') {
        const taskId = path.split('/')[2];
        return await getTaskImage(taskId, env);
      } else {
        return new Response(JSON.stringify({ error: 'Not found' }), {
          status: 404,
          headers: { 'Content-Type': 'application/json' }
        });
      }
    }
  };

/**
 * 获取任务列表
 */
async function getTasks(request, env) {
  try {
    // 解析查询参数
    const url = new URL(request.url);
    const status = url.searchParams.get('status');
    const page = parseInt(url.searchParams.get('page') || '1', 10);
    const perPage = parseInt(url.searchParams.get('per_page') || '10', 10);
    
    // 获取任务列表
    const tasksList = [];
    const taskKeys = await env.pic_analyzer.list({ prefix: 'task:' });
    
    // 过滤掉图像数据键
    const filteredKeys = taskKeys.keys.filter(key => !key.name.includes(':image'));
    
    // 获取所有任务数据
    const tasksPromises = filteredKeys.map(async key => {
      const taskData = await env.pic_analyzer.get(key.name, { type: 'json' });
      if (taskData) {
        // 添加任务ID
        const taskId = key.name.replace('task:', '');
        return { 
          id: taskId,
          ...taskData 
        };
      }
      return null;
    });
    
    let tasks = await Promise.all(tasksPromises);
    tasks = tasks.filter(task => task !== null);
    
    // 按创建时间排序（最新的在前）
    tasks.sort((a, b) => {
      return new Date(b.created_at) - new Date(a.created_at);
    });
    
    // 根据状态过滤
    if (status) {
      tasks = tasks.filter(task => task.status === status);
    }
    
    // 计算总数和分页
    const total = tasks.length;
    const totalPages = Math.ceil(total / perPage);
    const startIndex = (page - 1) * perPage;
    const endIndex = startIndex + perPage;
    const paginatedTasks = tasks.slice(startIndex, endIndex);
    
    return new Response(JSON.stringify({
      tasks: paginatedTasks,
      total: total,
      page: page,
      per_page: perPage,
      total_pages: totalPages
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' }
    });
  } catch (error) {
    console.error(`Error in getTasks: ${error.message}`);
    return new Response(JSON.stringify({ error: error.message }), {
      status: 500,
      headers: { 'Content-Type': 'application/json' }
    });
  }
}

/**
 * 获取任务图片
 */
async function getTaskImage(taskId, env) {
  try {
    // 获取图像数据
    const imageData = await env.pic_analyzer.get(`task:${taskId}:image`);
    
    if (!imageData) {
      return new Response(JSON.stringify({ error: 'Image not found' }), {
        status: 404,
        headers: { 'Content-Type': 'application/json' }
      });
    }
    
    // 将 base64 转换为二进制
    const byteCharacters = atob(imageData.includes(',') ? imageData.split(',', 2)[1] : imageData);
    const byteArrays = [];
    for (let i = 0; i < byteCharacters.length; i++) {
      byteArrays.push(byteCharacters.charCodeAt(i));
    }
    
    // 创建 Blob 对象
    const blob = new Blob([new Uint8Array(byteArrays)], { type: 'image/png' });
    
    // 返回图片
    return new Response(blob, {
      status: 200,
      headers: { 
        'Content-Type': 'image/png',
        'Cache-Control': 'public, max-age=86400'
      }
    });
  } catch (error) {
    console.error(`Error in getTaskImage: ${error.message}`);
    return new Response(JSON.stringify({ error: error.message }), {
      status: 500,
      headers: { 'Content-Type': 'application/json' }
    });
  }
}