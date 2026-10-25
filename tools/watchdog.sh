#!/bin/bash
# ===== NapCat QQ 掉线看门狗 =====
# 检测掉线 -> 自动重启napcat(快速登录) -> 3次熔断 -> 夜间静默 -> 可选微信推送
# cron: */2 * * * * flock -n /tmp/qqup.lock /root/eidolon/watchdog.sh >> /root/eidolon/watchdog.log 2>&1

WEBUI_TOKEN="<NAPCAT_WEBUI_TOKEN>"
STATE=/root/eidolon/watchdog.state
RESTART_LOG=/root/eidolon/watchdog.restarts
CONF=/root/eidolon/watchdog.conf          # 可选: SENDKEY="SCTxxxx"
NIGHT_START=2; NIGHT_END=7                # 夜间静默时段(不重启只记录)
MAX_RESTARTS=3; WINDOW=3600               # 1小时内最多3次重启,超出熔断

[ -f "$CONF" ] && source "$CONF"
log() { echo "[$(date '+%m-%d %H:%M:%S')] $*"; }
push() {  # ServerChan微信推送(未配SENDKEY则跳过)
  [ -n "$SENDKEY" ] && curl -s -m 8 "https://sctapi.ftqq.com/${SENDKEY}.send" \
    -d "title=$1" -d "desp=$2" >/dev/null 2>&1
  return 0
}

# ---- 1. 查状态: online / offline / error ----
H=$(python3 -c "import hashlib;print(hashlib.sha256('${WEBUI_TOKEN}.napcat'.encode()).hexdigest())")
CRED=$(curl -s -m 6 -X POST -H "Content-Type: application/json" \
  -d "{\"hash\":\"$H\"}" http://127.0.0.1:6099/api/auth/login 2>/dev/null | \
  python3 -c "import sys,json;print(json.load(sys.stdin)['data']['Credential'])" 2>/dev/null)
if [ -z "$CRED" ]; then CUR=error
else
  RESP=$(curl -s -m 6 -X POST -H "Authorization: Bearer $CRED" -H "Content-Type: application/json" \
    -d '{}' http://127.0.0.1:6099/api/QQLogin/CheckLoginStatus 2>/dev/null)
  VERDICT=$(echo "$RESP" | python3 -c "
import sys,json
try:
  d=json.load(sys.stdin)['data']
  print('online' if (d.get('isLogin') and not d.get('isOffline') and d.get('coreReady')) else 'offline')
except Exception:
  print('error')" 2>/dev/null)
  CUR=${VERDICT:-error}
fi
PREV=$(cat "$STATE" 2>/dev/null || echo online)
echo "$CUR" > "$STATE"

# ---- 2. 状态机 ----
if [ "$CUR" = online ]; then
  [ "$PREV" != online ] && [ "$PREV" != "" ] && { log "已恢复上线"; push "QQ机器人已恢复" "bot重新上线了"; }
  exit 0
fi
if [ "$CUR" = error ]; then
  # 读不到状态(token/网络/napcat没起): 只提醒不重启, 避免误判
  [ "$PREV" != error ] && log "WARN 状态接口异常(不重启,需人工检查)"
  exit 0
fi

# ---- CUR=offline ----
HOUR=$(date +%H)
NIGHT=0
if [ $NIGHT_START -le $NIGHT_END ]; then
  [ $HOUR -ge $NIGHT_START ] && [ $HOUR -lt $NIGHT_END ] && NIGHT=1
fi
# 清理窗口外的重启记录
NOW=$(date +%s)
touch "$RESTART_LOG"
awk -v n=$NOW -v w=$WINDOW '$1 > n-w' "$RESTART_LOG" > "$RESTART_LOG.tmp" && mv "$RESTART_LOG.tmp" "$RESTART_LOG"
CNT=$(wc -l < "$RESTART_LOG")

if [ $CNT -ge $MAX_RESTARTS ]; then
  [ "$PREV" != breaker ] && { log "FUSE 熔断: ${WINDOW}s内已重启${CNT}次仍离线,停止自动重启转人工(去WebUI 6099扫码)"; push "QQ机器人掉线-需人工" "自动重登多次失败已熔断,请扫码: http://1.95.144.92:6099/webui"; echo breaker > "$STATE"; }
  exit 0
fi
if [ $NIGHT -eq 1 ]; then
  [ "$PREV" != night ] && { log "夜间静默: 掉线但不重启(风控高发期),早7点后自动处理"; echo night > "$STATE"; }
  exit 0
fi

# 冷却: 上次重启90秒内不重复
LAST=$(tail -1 "$RESTART_LOG" | cut -d' ' -f1)
[ -n "$LAST" ] && [ $((NOW-LAST)) -lt 90 ] && exit 0

log "OFFLINE 掉线 detected(prev=$PREV) -> 重启napcat(第$((CNT+1))/${MAX_RESTARTS}次)"
echo "$NOW" >> "$RESTART_LOG"
docker restart napcat >/dev/null 2>&1
log "napcat已重启,等待快速登录..."
push "QQ机器人掉线" "已自动重启napcat尝试快速登录(第$((CNT+1))次)"
