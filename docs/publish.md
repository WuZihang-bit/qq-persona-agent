# 开源发布操作手册

自己从零发布/更新一个 GitHub 开源项目的完整流程。照抄命令即可。

## 一、首次发布（仓库从本地推上去）

### 1. 发布前自查（每次都要做）

```bash
cd /f/qq-digital-twin

# a) 看看哪些文件会被提交
git status
git ls-files

# b) 敏感信息全库扫描（应无输出）
grep -rn "你的QQ号\|你的名字\|sk-\|tvly-\|api_key.*[a-z0-9]\{20\}" . --include="*.py" --include="*.yaml" --include="*.md" | grep -v example

# c) 确认 .gitignore 拦住了该拦的（config.yaml、*.db、*key.txt 等）
```

> 原则：**key 永远只放 config.yaml（已 ignore）和 GitHub Secrets；真名/QQ号只出现在本地。**

### 2. GitHub 上建仓库

1. 登录 github.com → 右上角 **+** → **New repository**
2. Repository name：如 `qq-digital-twin`
3. **不要**勾选 "Add a README / .gitignore / license"（会和本地冲突）
4. Public → Create repository
5. 创建后页面会显示一段命令，忽略它，用下面的

### 3. 关联远程并推送（只需做一次）

```bash
git remote add origin https://github.com/<你的用户名>/qq-digital-twin.git
git branch -M main
git push -u origin main
```

首次 push 会弹出浏览器要求登录 GitHub 授权（Windows 凭据管理器会记住，之后不再弹）。

### 4. 发一个版本标签（可选但推荐）

```bash
git tag -a v0.1 -m "first release"
git push origin v0.1
```

然后 GitHub 仓库页 → Releases → Draft a new release → 选 v0.1 标签 → 写更新说明 → Publish。

## 二、日常更新循环（每次改完代码）

```bash
git add -A
git commit -m "feat: 修复了xxx"        # 前缀习惯: feat/fix/docs/chore
git push
```

三行命令就是全部。养成先 `git status` 看一眼的习惯，防止误提交本地数据文件。

## 三、迭代新版本

```bash
# 开发 → 自查(第一步的 a/b/c) → 提交推送 → 打新 tag
git tag -a v0.2 -m "蒸馏工具链"
git push origin v0.2
# → GitHub Releases 写 changelog
```

版本号习惯（语义化）：`v0.x` 阶段开发；功能成熟到 1.0 再叫 `v1.0`。
破坏性改动升次版本号（v0.2→v0.3 可以随便断），1.0 之后破坏性改动升 v2.0。

## 四、让人找到你的项目

1. **发一篇介绍文章**（最重要，比代码质量更引流）：
   - 知乎/掘金/B站：标题参考《我把自己的 1.7 万条聊天记录蒸馏成了 QQ 机器人》
   - 内容结构：效果演示截图 → 原理架构图 → 踩坑故事（被风控踢、密钥不在文件里）→ 仓库链接
   - 注意截图里不能露真实昵称/头像/QQ号
2. **投放渠道**：V2EX（分享创造）、GitHub Trending（靠 star）、相关 awesome 列表提 PR
3. **README 顶部放演示动图**（GIF 比 N 张截图有效十倍）

## 五、社区运营（有人来之后）

- **Issue**：别人报 bug，尽量复现 → 修复 → 在 issue 里回复版本号
- **PR**：别人提代码，review 后 merge；不合意的也礼貌回复
- 加 `CONTRIBUTING.md`（可选）：说明怎么跑起来、代码风格
- 别人的 PR 里的敏感信息自己有责任把关（尤其聊天记录/截图）

## 六、出事怎么办

- **误提交了 key**：立刻去服务商后台**作废该 key**（这是唯一正确操作），然后从历史中清除：
  ```bash
  git filter-repo --replace-text <(echo "旧key==>REDACTED")   # 需 pip install git-filter-repo
  git push --force
  ```
  光删文件再提交没用，历史里还在。
- **误提交了隐私数据**：同理 force push 清历史；已经被人 fork 的话，当作已泄露处理。
- **被人提 DMCA/侵权**（表情包等）：删内容 + 道歉 + 换自制素材。

## 速查卡

```bash
# 日常三连
git add -A && git commit -m "fix: xxx" && git push

# 看历史
git log --oneline

# 看某次改了什么
git show <commit前几位>

# 撤销工作区未提交的改动（危险，慎用）
git checkout -- <文件>

# 版本发布
git tag -a v0.x -m "说明" && git push origin v0.x
```
