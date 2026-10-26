// Cloudflare Worker: TypeSafe API 大陆中转
// 部署: dash.cloudflare.com -> Workers -> Create -> 粘贴本文件 -> Deploy
// 然后在服务器 /root/eidolon/data/fensheng/typesafe_key.json 把 api_base 改成
//   "https://你的worker名.你的子域.workers.dev"
export default {
  async fetch(req) {
    if (req.method !== "POST") return new Response("POST only", { status: 405 });
    const url = new URL(req.url);
    const upstream = "https://api.typesafe.ai" + url.pathname;
    const r = await fetch(upstream, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: req.headers.get("Authorization") || "",
      },
      body: await req.text(),
    });
    return new Response(r.body, { status: r.status, headers: { "Content-Type": "application/json" } });
  },
};
