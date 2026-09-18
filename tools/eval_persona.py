# -*- coding: utf-8 -*-
"""人格一致性评测框架
用法: python eval_persona.py --persona personas/example/persona.txt --cases eval_cases.json --out report.md
流程: 逐条用例 → 被测模型带人格生成回复 → 裁判模型按评分标准打分 → 输出报告
"""
import argparse, json, os, time
import requests

GLM_KEY = os.environ.get("GLM_KEY", "填你的key")
GLM_BASE = "https://open.bigmodel.cn/api/paas/v4"
CAND_MODEL = os.environ.get("CAND_MODEL", "glm-5.3-flash")
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "glm-5.3-flash")

JUDGE_PROMPT = '''你是人格一致性评测裁判。给定"人设描述"和"AI扮演该人设的回复"，按5分制打分：
5 = 完全符合人设的语言风格和性格，无法分辨是AI
4 = 大体符合，个别地方出戏
3 = 一半符合，风格漂移明显
2 = 勉强像，主要靠内容而非风格
1 = 完全没有人设感，通用AI腔
另检查"硬违规"：说出真实姓名/长篇大论不像短句党/动作描写/列点解释 → 直接2分以下。
只输出JSON：{"score": 1-5, "reason": "一句话理由"}'''


def chat(model, messages, temperature=0.7):
    r = requests.post(f"{GLM_BASE}/chat/completions", headers={"Authorization": "Bearer " + GLM_KEY},
                      json={"model": model, "messages": messages, "temperature": temperature}, timeout=90)
    return r.json()["choices"][0]["message"]["content"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--persona", required=True)
    ap.add_argument("--cases", required=True)
    ap.add_argument("--out", default="eval_report.md")
    args = ap.parse_args()

    persona = open(args.persona, encoding="utf-8").read()
    cases = json.load(open(args.cases, encoding="utf-8"))
    results = []
    for i, c in enumerate(cases):
        user, tag = c["user"], c.get("tag", "")
        reply = chat(CAND_MODEL, [{"role": "system", "content": persona},
                                  {"role": "user", "content": user}]).strip()
        judge_raw = chat(JUDGE_MODEL,
                         [{"role": "system", "content": JUDGE_PROMPT},
                          {"role": "user", "content": f"[人设]\n{persona[:1500]}\n\n[用户消息]\n{user}\n\n[AI回复]\n{reply}"}],
                         temperature=0.1)
        try:
            j = json.loads(judge_raw[judge_raw.find("{"): judge_raw.rfind("}") + 1])
        except Exception:
            j = {"score": 0, "reason": f"裁判解析失败: {judge_raw[:80]}"}
        results.append({"case": i + 1, "tag": tag, "user": user, "reply": reply[:200],
                        "score": j.get("score", 0), "reason": j.get("reason", "")})
        print(f"[{i+1}/{len(cases)}] {j.get('score')}分 | {user[:30]} -> {reply[:40]}")
        time.sleep(1)

    scores = [r["score"] for r in results]
    avg = sum(scores) / len(scores)
    lines = [f"# 人格一致性评测报告", f"",
             f"- 被测模型: `{CAND_MODEL}`",
             f"- 用例数: {len(cases)}",
             f"- **平均分: {avg:.2f} / 5**",
             f"- 4分及以上占比: {sum(1 for s in scores if s >= 4)/len(scores):.0%}",
             "", "| # | 类型 | 用户消息 | AI回复 | 分 | 裁判理由 |", "|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['case']} | {r['tag']} | {r['user'][:30]} | {r['reply'][:50]} | {r['score']} | {r['reason'][:60]} |")
    open(args.out, "w", encoding="utf-8").write("\n".join(lines))
    print(f"\n平均分 {avg:.2f}/5, 报告 -> {args.out}")


if __name__ == "__main__":
    main()
