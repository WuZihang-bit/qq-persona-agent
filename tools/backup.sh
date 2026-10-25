#!/bin/bash
# 每日备份: astrbot数据+napcat配置+QQ登录卷+编排文件, 保留7天
# cron: 10 4 * * * /root/eidolon/backup.sh >> /root/eidolon/backup.log 2>&1
set -e
DST=/root/backups
DAY=$(date +%Y%m%d)
mkdir -p "$DST"
# QQ登录态卷(docker volume)
QQVOL=/var/lib/docker/volumes/eaf4a9a87ed54a1b1dcfbf5777121f1989e4587f60c4debd03d9a88b37972f99/_data
tar czf "$DST/eidolon_$DAY.tar.gz" \
    -C / root/eidolon/data root/eidolon/napcat root/eidolon/docker-compose.yml \
    root/eidolon/watchdog.sh root/eidolon/backup.sh 2>/dev/null || true
[ -d "$QQVOL" ] && tar czf "$DST/qqlogin_$DAY.tar.gz" -C "$QQVOL" .. 2>/dev/null || true
# 7天轮换
find "$DST" -name '*.tar.gz' -mtime +7 -delete
echo "[$(date '+%m-%d %H:%M')] backup ok: $(du -sh $DST/eidolon_$DAY.tar.gz 2>/dev/null | cut -f1)"
