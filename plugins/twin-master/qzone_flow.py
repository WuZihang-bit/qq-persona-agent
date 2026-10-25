# -*- coding: utf-8 -*-
"""发空间任务流: 独立于事件生命周期, 用NapCat接口直接发送
三种内容类型: 诗 / 看法 / 知识, 均配图发布(公开)
生图链: 随想gpt-image-2 → CogView → 纯文字 (可灵未充API额度, 暂不进链)
跨容器注意: astrbot与napcat不共享存储, 图片必须以 base64:// 传给 send_qzone_msg
"""
import asyncio
import os

async def ca(bot, action, payload):
    """aiocqhttp call_action 只收kwargs, 此包装接受dict"""
    return await bot.call_action(action, **payload)

import base64
import io
import time
import requests

GLM_KEY = os.environ.get('GLM_KEY', '')
GLM_BASE = 'https://open.bigmodel.cn/api/paas/v4'
KLING_KEY = os.environ.get('KLING_KEY', '')
KLING_BASE = 'https://api-beijing.klingai.com'
GPT_IMG_KEY = os.environ.get('GPT_IMG_KEY', '')
GPT_IMG_BASE = 'https://www.sui-xiang.net/v1'
GPT_IMG_MODELS = ['gpt-image-2', 'gpt-image-2.5-flare', 'gpt-image-1']
NL = chr(10)

PROMPTS = {
    '诗': {
        'sys': '你是擅长意象的现代诗人, 不押韵不煽情。',
        'user': '以「{topic}」为主题写一首现代诗: 6~10行, 重意象和氛围感, 有具体画面, 最后一行留白。只输出诗本身。',
    },
    '看法': {
        'sys': '你以主人的口吻说话: 口语化, 有观点有态度, 偶尔毒舌但讲理。',
        'user': '写一段发QQ空间的「看法」: 关于{topic}, 3~6句话, 像真人发的朋友圈感悟, 有一句点睛的观点。只输出正文。',
    },
    '知识': {
        'sys': '你是博学但说话通俗的知识分享者。',
        'user': '讲一个关于「{topic}」的冷知识或知识点, 100字以内, 口语化, 结尾让人有"学到了"的感觉。只输出正文。',
    },
}

IMG_SYS = '你是绘画提示词工程师, 只输出画面描述。'


async def llm(llm_call, prompt, system):
    return await llm_call(prompt, system)


async def send(bot, ctx_type, ctx_id, text, image_path=None):
    msg = [{'type': 'text', 'data': {'text': text[:1500]}}]
    if image_path:
        b64 = open(image_path, 'rb').read()
        import base64
        msg.append({'type': 'image', 'data': {'file': 'base64://' + base64.b64encode(b64).decode()}})
    fn = 'send_group_msg' if ctx_type == 'group' else 'send_private_msg'
    key = 'group_id' if ctx_type == 'group' else 'user_id'
    payload = {key: int(ctx_id), 'message': msg}
    for i in range(3):
        try:
            await ca(bot, fn, payload)
            return
        except Exception:
            if i == 2:
                raise
            await asyncio.sleep(2 * (i + 1))


def _req_retry(fn, tries=3, base_delay=3):
    """网络请求重试: 3次指数退避, 穿越瞬时网络故障"""
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            last = e
            time.sleep(base_delay * (2 ** i))
    raise last


def _gpt_once(desc):
    """随想中转 gpt-image 生图, 依次试模型直到成功, 返回 {'b64':..} 或 {'url':..}"""
    last = None
    for m in GPT_IMG_MODELS:
        try:
            r = requests.post(
                GPT_IMG_BASE + '/images/generations',
                headers={'Authorization': 'Bearer ' + GPT_IMG_KEY}, timeout=300,
                json={'model': m, 'prompt': desc, 'size': '1024x1024', 'quality': 'low'})
        except Exception as e:
            last = e
            continue
        if r.status_code == 200:
            d = (r.json().get('data') or [{}])[0]
            if d.get('b64_json'):
                return {'b64': d['b64_json']}
            if d.get('url'):
                return {'url': d['url']}
            last = Exception('gpt-image返回无图数据')
        else:
            last = Exception(m + ' http' + str(r.status_code) + ': ' + r.text[:120])
    raise last or Exception('gpt生图失败')


def _kling_once(desc):
    """可灵生图(当前未充API额度不进链, 充值后加回gen_image链尾即可), 兼容同步url/b64和异步task两种返回"""
    r = requests.post(
        KLING_BASE + '/v1/images/generations',
        headers={'Authorization': 'Bearer ' + KLING_KEY}, timeout=180,
        json={'model': 'kolors', 'prompt': desc, 'aspect_ratio': '1:1'})
    try:
        j = r.json()
    except Exception:
        raise Exception('kling http' + str(r.status_code) + ': ' + r.text[:150])
    if r.status_code >= 400:
        raise Exception('kling err: ' + str(j)[:200])
    d = j.get('data')
    if isinstance(d, list) and d and isinstance(d[0], dict):
        if d[0].get('url'):
            return {'url': d[0]['url']}
        if d[0].get('b64_json'):
            return {'b64': d[0]['b64_json']}
    if isinstance(d, dict) and d.get('task_id'):
        tid = d['task_id']
        for _ in range(40):
            time.sleep(4)
            q = requests.get(KLING_BASE + '/v1/images/generations/' + tid,
                             headers={'Authorization': 'Bearer ' + KLING_KEY},
                             timeout=30).json()
            st = (q.get('data') or {}).get('task_status', '')
            if st == 'succeed':
                imgs = ((q.get('data') or {}).get('task_result') or {}).get('images') or []
                if imgs and imgs[0].get('url'):
                    return {'url': imgs[0]['url']}
                raise Exception('kling task无图: ' + str(q)[:150])
            if st == 'failed':
                raise Exception('kling task失败: ' + str(q)[:200])
        raise Exception('kling task超时')
    raise Exception('kling 返回无法解析: ' + str(j)[:200])


def _cogview_once(desc):
    r = requests.post(
        GLM_BASE + '/images/generations',
        headers={'Authorization': 'Bearer ' + GLM_KEY}, timeout=120,
        json={'model': 'cogview-3-flash', 'prompt': desc, 'size': '1024x1024'})
    return {'url': r.json()['data'][0]['url']}


def _to_jpeg(data):
    """压成JPEG(q85,长边1600)减小base64体积; PIL不可用则原样返回"""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data)).convert('RGB')
        if max(im.size) > 1600:
            im.thumbnail((1600, 1600))
        buf = io.BytesIO()
        im.save(buf, 'JPEG', quality=85)
        return buf.getvalue()
    except Exception:
        return data


async def gen_image(desc):
    """生图: 随想gpt-image优先, CogView兜底; 全失败抛异常由上层降级纯文本"""
    result, src = None, ''
    for name, fn, tries in (('gpt', _gpt_once, 2), ('cogview', _cogview_once, 1)):
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda f=fn, t=tries, d=desc: _req_retry(lambda: f(d), tries=t, base_delay=2))
            src = name
            break
        except Exception:
            continue
    if not result:
        raise Exception('gpt/cogview 生图均失败')

    def _dl():
        if result.get('b64'):
            return base64.b64decode(result['b64'])
        return requests.get(result['url'], timeout=60).content
    data = await asyncio.get_event_loop().run_in_executor(None, _dl)
    data = _to_jpeg(data)
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gen_tmp',
                        'qz_' + str(int(time.time() * 1000)) + '.jpg')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, 'wb').write(data)
    print('[qzone_flow] 生图成功 via ' + src + ' -> ' + path + ' (' + str(len(data)) + ' bytes)')
    return path


async def run_flow(bot, provider, ctx_type, ctx_id, kind, topic):
    """完整流程: 生成内容 → 生成配图 → NapCat发空间 → 回报。返回(成功?, 汇报文本)"""
    cfg = PROMPTS.get(kind, PROMPTS['诗'])

    async def llm_call(prompt, system):
        last = None
        for attempt in range(3):
            try:
                r = await provider.text_chat(prompt=prompt, system_prompt=system)
                out = (getattr(r, 'completion_text', '') or '').strip()
                if out:
                    return out
                last = Exception('LLM返回空')
            except Exception as e:
                last = e
            await asyncio.sleep(3 * (attempt + 1))    # 3s/6s
        raise last or Exception('LLM三次尝试均失败')

    try:
        await send(bot, ctx_type, ctx_id, '开始创作: ' + kind + ' - ' + topic + ' ,画图大约需要2~3分钟..')
        content = await llm(llm_call, cfg['user'].format(topic=topic), cfg['sys'])
        img_desc = await llm(llm_call,
                             '提取下面内容的视觉意象, 输出60字内的中文画面描述(场景/光影/色调), 只输出描述:' + NL + content,
                             IMG_SYS)
        try:
            img_path = await gen_image(img_desc)
        except Exception as ge:
            img_path = None
            print('[qzone_flow] 生图失败, 降级纯文本发布: ' + str(ge)[:150])
        payload = {'content': content, 'ugc_right': 1}
        if img_path:
            img_b64 = base64.b64encode(open(img_path, 'rb').read()).decode()
            payload['images'] = ['base64://' + img_b64]
        r = await ca(bot, 'send_qzone_msg', payload)
        tid = (r or {}).get('tid', '')
        note = '[配图意象] ' + img_desc if img_path else '[无图: 生图服务不可用]'
        report = ('已发到空间(公开) tid=' + tid + NL + NL + content + NL + NL + note)
        await send(bot, ctx_type, ctx_id, report)
        return True, report
    except Exception as e:
        try:
            await send(bot, ctx_type, ctx_id, '流水线出错: ' + str(e)[:200])
        except Exception:
            pass
        return False, str(e)
