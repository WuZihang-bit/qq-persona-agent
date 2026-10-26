// Val Town 中转(免费, 邮箱注册 https://www.val.town -> New val -> HTTP val -> 粘贴 -> Save)
// 记得把 RELAY_KEY 换成你在 typesafe_key.json 里配的那个
const RELAY_KEY = "rk_cb7cc3ff25826552adbf1f8d";

export default async function(req: Request): Promise<Response> {
  if (req.headers.get("X-Relay-Key") !== RELAY_KEY)
    return new Response("forbidden", { status: 403 });
  const url = new URL(req.url);
  const r = await fetch("https://api.typesafe.ai" + url.pathname, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: req.headers.get("Authorization") ?? "",
    },
    body: await req.text(),
  });
  return new Response(r.body, { status: r.status, headers: { "Content-Type": "application/json" } });
}
