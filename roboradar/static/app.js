/* RoboRadar 前端：原生 JS 单页应用 */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, opts = {}) {
  const init = { headers: { "Content-Type": "application/json" }, ...opts };
  if (init.body && typeof init.body !== "string") init.body = JSON.stringify(init.body);
  const r = await fetch(path, init);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (_) {}
    throw new Error(msg);
  }
  const ct = r.headers.get("content-type") || "";
  return ct.includes("json") ? r.json() : r.text();
}

let toastTimer;
function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (err ? " err" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), err ? 6000 : 3000);
}

function openModal(html) {
  $("#modal-box").innerHTML = html;
  $("#modal").classList.remove("hidden");
}
function closeModal() { $("#modal").classList.add("hidden"); }
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });

function timeAgo(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  const s = (Date.now() - d) / 1000;
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))} 分钟前`;
  if (s < 86400) return `${Math.round(s / 3600)} 小时前`;
  if (s < 86400 * 30) return `${Math.round(s / 86400)} 天前`;
  return d.toLocaleDateString("zh-CN");
}
const fmtTime = (iso) => (iso ? new Date(iso).toLocaleString("zh-CN", { hour12: false }) : "—");

/* ================= 路由 ================= */
const state = { view: "digest", digestDate: null, showAll: false, tagFilter: null, q: "", sort: "relevance",
  feed: { days: 14, entity: "", type: "", unread: false, baseline: false }, settings: null };
const views = {};

function go(view) {
  state.view = view;
  $$("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.view === view));
  location.hash = view;
  views[view]().catch((e) => {
    $("#view").innerHTML = `<div class="empty"><div class="big">⚠️</div>页面加载失败：${esc(e.message)}</div>`;
  });
}
$$("#nav a").forEach((a) => a.addEventListener("click", () => go(a.dataset.view)));

/* ================= 任务状态轮询 ================= */
const JOB_NAME = { arxiv: "arXiv 论文汇总", watch: "关注列表抓取" };
let wasRunning = {};
async function pollStatus() {
  try {
    const st = await api("/api/status");
    const bar = $("#jobbar");
    const running = Object.entries(st.progress).filter(([, p]) => p.running);
    bar.innerHTML = running.map(([k, p]) => {
      const pct = p.total ? Math.round((p.done / p.total) * 100) : 8;
      return `<div class="job"><span class="spin">⏳</span><b>${JOB_NAME[k]}</b><span>${esc(p.stage || "")}${p.total ? ` ${p.done}/${p.total}` : ""}</span>
        <span class="bar"><i style="width:${pct}%"></i></span><span class="msg">${esc(p.message || "")}</span></div>`;
    }).join("");
    bar.classList.toggle("hidden", running.length === 0);
    // 任务刚结束：提示并刷新当前页
    for (const [k, p] of Object.entries(st.progress)) {
      if (wasRunning[k] && !p.running) {
        toast(`${JOB_NAME[k]}${p.stage === "失败" ? "失败：" : "完成："}${p.message || ""}`, p.stage === "失败");
        if ((k === "arxiv" && state.view === "digest") || (k === "watch" && ["feed", "entities"].includes(state.view))) views[state.view]();
        refreshBadge();
      }
      wasRunning[k] = p.running;
    }
    const sch = st.next_runs.slice(0, 6).map((j) => `<div>${j.kind === "arxiv" ? "📄" : "📺"} ${j.next ? new Date(j.next).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }) : "—"}</div>`).join("");
    $("#schedule").innerHTML = `<b>下次运行</b>${sch || "<div>未设置定时</div>"}`;
    setTimeout(pollStatus, running.length ? 1500 : 5000);
  } catch (e) {
    setTimeout(pollStatus, 8000);
  }
}
async function refreshBadge() {
  try {
    const items = await api("/api/feed?days=3&unread=1&limit=500");
    const n = items.filter((i) => !i.baseline).length;
    const b = $("#feed-badge");
    b.textContent = n;
    b.classList.toggle("hidden", n === 0);
  } catch (_) {}
}

async function runJob(kind, body = {}) {
  try {
    await api(`/api/run/${kind}`, { method: "POST", body });
    wasRunning[kind] = true;
    toast(`已开始：${JOB_NAME[kind]}`);
    setTimeout(pollStatus, 300);
  } catch (e) { toast(e.message, true); }
}

/* ================= 论文日报 ================= */
function paperCard(p, i, opts = {}) {
  const corr = p.corresponding?.length ? p.corresponding : [];
  const authors = p.authors.slice(0, 3).map((a) => (corr.includes(a) ? `<span class="corr">${esc(a)}✉</span>` : esc(a))).join(", ")
    + (p.authors.length > 3 ? ` 等 ${p.authors.length} 人` : "");
  const corrText = corr.length ? corr.map((c) => `<span class="corr">${esc(c)}</span>`).join(", ") : '<span class="kw">未标注</span>';
  const tags = (p.tags?.length ? p.tags : p.matched_topics || []).map((t) => `<span class="tag">${esc(t)}</span>`).join("")
    + (p.platform ? `<span class="tag plat">🤖 ${esc(p.platform)}</span>` : "");
  const status = p.llm_status === "rule" ? '<span class="tag warn" title="未使用 LLM，规则提取">规则提取</span>'
    : p.llm_status === "error" ? `<span class="tag err" title="${esc(p.llm_error)}">LLM 失败</span>`
    : p.llm_status === "pending" ? '<span class="tag grey">未总结</span>' : "";
  const semantic = (p.semantic && !p.matched ? '<span class="tag warn">语义补选</span>' : "")
    + (p.llm_status === "ok" && p.relevance <= 3 ? `<span class="tag grey" title="LLM 判断相关度 ${p.relevance}/10">低相关</span>` : "");
  const links = [`<a href="https://arxiv.org/abs/${p.arxiv_id}" target="_blank">arXiv</a>`,
    `<a href="https://arxiv.org/pdf/${p.arxiv_id}" target="_blank">PDF</a>`,
    `<a href="https://arxiv.org/html/${p.arxiv_id}" target="_blank">HTML</a>`,
    ...(p.links || []).map((l) => `<a href="${esc(l.url)}" target="_blank">${l.label === "代码" ? "💻" : "🌐"} ${esc(l.label)}</a>`)].join("");
  const pending = p.llm_status === "pending" && !(p.matched || p.semantic);
  return `<div class="paper ${p.read ? "read" : ""}" data-id="${p.arxiv_id}">
    <div class="head"><div class="idx">${i}</div>
      <div class="ttl"><a class="t" href="https://arxiv.org/abs/${p.arxiv_id}" target="_blank" data-act="open">${esc(p.title)}</a>
        ${p.title_zh ? `<div class="zh">${esc(p.title_zh)}</div>` : ""}</div>
      <div class="acts">
        <button class="ghost" data-act="star" title="收藏">${p.starred ? "⭐" : "☆"}</button>
        <button class="ghost" data-act="read" title="标记已读/未读">${p.read ? "✅" : "◻️"}</button>
        ${pending ? '<button class="ghost" data-act="include" title="收录并总结">➕ 收录</button>' : '<button class="ghost" data-act="resum" title="重新解析并总结">🔄</button>'}
      </div></div>
    <div class="row"><span class="lbl">👥 作者</span>${authors}　<span class="lbl">✉ 通讯</span>${corrText}</div>
    ${p.affiliations?.length ? `<div class="row"><span class="lbl">🏛 单位</span>${p.affiliations.map(esc).join("；")}</div>` : ""}
    <div class="row">${tags}${semantic}${status}</div>
    ${p.method || p.result ? `<div class="summary">${p.method ? `<div>🔧 <b>方法</b>　${esc(p.method)}</div>` : ""}${p.result ? `<div>📊 <b>结果</b>　${esc(p.result)}</div>` : ""}</div>`
      : (pending ? `<div class="summary kw">${esc(p.abstract.slice(0, 300))}…</div>` : "")}
    <div class="links">${links}${p.matched_keywords?.length ? `<span class="kw">命中：${p.matched_keywords.map(esc).join(" · ")}</span>` : ""}
      ${opts.showDate ? `<span class="kw">${p.digest_date}</span>` : ""}</div>
  </div>`;
}

function bindPaperActions(root, list, rerender) {
  root.onclick = async (e) => {
    const btn = e.target.closest("[data-act]");
    if (!btn) return;
    const card = btn.closest(".paper");
    const p = list.find((x) => x.arxiv_id === card.dataset.id);
    const act = btn.dataset.act;
    try {
      if (act === "star" || act === "read") {
        const field = act === "star" ? "starred" : "read";
        p[field] = p[field] ? 0 : 1;
        await api(`/api/papers/${p.arxiv_id}/flag`, { method: "POST", body: { field, value: p[field] } });
        rerender();
      } else if (act === "open") {
        if (!p.read) { p.read = 1; api(`/api/papers/${p.arxiv_id}/flag`, { method: "POST", body: { field: "read", value: 1 } }); }
      } else if (act === "resum" || act === "include") {
        btn.disabled = true; btn.innerHTML = '<span class="spin">⏳</span>';
        toast("正在解析论文并生成总结…");
        const np = await api(`/api/papers/${p.arxiv_id}/summarize`, { method: "POST", body: { include: act === "include" } });
        Object.assign(p, np);
        rerender();
        toast("已更新总结");
      }
    } catch (err) { toast(err.message, true); rerender(); }
  };
}

views.digest = async () => {
  const v = $("#view");
  v.innerHTML = '<div class="empty"><span class="spin">⏳</span> 加载中…</div>';
  const [dates, settings] = await Promise.all([api("/api/digest/dates"), state.settings ? state.settings : api("/api/settings")]);
  state.settings = settings;
  const topicNames = new Set(settings.topics.filter((t) => t.enabled !== false).map((t) => t.name));
  if (!state.digestDate && dates.length) state.digestDate = dates[0].date;
  const data = state.digestDate ? await api(`/api/digest?date=${state.digestDate}&all=${state.showAll ? 1 : 0}`) : { papers: [], total: 0 };
  const papers = data.papers;
  const related = papers.filter((p) => p.matched || p.semantic).length;
  const tagCount = {};
  papers.forEach((p) => (p.tags?.length ? p.tags : p.matched_topics || []).forEach((t) => (tagCount[t] = (tagCount[t] || 0) + 1)));
  const platCount = {};
  papers.forEach((p) => { if (p.platform && /实机/.test(p.platform)) platCount["有实机实验"] = (platCount["有实机实验"] || 0) + 1; });

  v.innerHTML = `
    <h1 class="title">📄 arXiv 论文日报</h1>
    <div class="subtitle">来源 <a href="https://arxiv.org/list/cs.RO/recent" target="_blank">arxiv.org/list/cs.RO/recent</a>，按你的关键词主题筛选，自动解析作者 / 通讯 / 单位并生成两句话总结</div>
    <div class="toolbar">
      <select id="d-date">${dates.map((d) => `<option value="${d.date}" ${d.date === state.digestDate ? "selected" : ""}>${d.date}（相关 ${d.related}/${d.total}）</option>`).join("") || "<option>暂无数据</option>"}</select>
      <button id="d-back">📅 回溯其它日期</button>
      <button class="primary" id="d-run">▶ 立即抓取最新</button>
      <span class="spacer"></span>
      <button id="d-force" title="按当前关键词重新匹配，并对所有相关论文重新总结">🔁 重新匹配并总结</button>
      <button id="d-md">⬇ Markdown</button>
      <button id="d-html">⬇ HTML</button>
      <button id="d-push">📤 推送</button>
    </div>
    ${state.digestDate ? `<div class="toolbar">
      <input class="search" id="d-q" type="text" placeholder="🔍 搜索标题 / 作者 / 单位 / 总结" value="${esc(state.q)}">
      <div class="chips" id="d-tags">
        <span class="chip ${!state.tagFilter ? "on" : ""}" data-tag="">全部 <small>${papers.length}</small></span>
        ${Object.entries(tagCount).filter(([t, n]) => topicNames.has(t) || n >= 2 || state.tagFilter === t).sort((a, b) => (topicNames.has(b[0]) - topicNames.has(a[0])) || b[1] - a[1]).map(([t, n]) => `<span class="chip ${state.tagFilter === t ? "on" : ""}" data-tag="${esc(t)}">${esc(t)} <small>${n}</small></span>`).join("")}
        ${platCount["有实机实验"] ? `<span class="chip ${state.tagFilter === "@real" ? "on" : ""}" data-tag="@real">🤖 有实机实验 <small>${platCount["有实机实验"]}</small></span>` : ""}
      </div>
      <span class="spacer"></span>
      <select id="d-sort"><option value="relevance">按相关度</option><option value="score">按关键词得分</option><option value="id">按编号</option></select>
      <label class="check"><input type="checkbox" id="d-all" ${state.showAll ? "checked" : ""}> 显示当天全部 ${data.total} 篇</label>
    </div>` : ""}
    <div id="d-list"></div>`;

  $("#d-sort") && ($("#d-sort").value = state.sort);
  const render = () => {
    const q = state.q.trim().toLowerCase();
    let list = papers.filter((p) => {
      if (state.tagFilter === "@real") { if (!/实机/.test(p.platform || "")) return false; }
      else if (state.tagFilter && !(p.tags?.length ? p.tags : p.matched_topics || []).includes(state.tagFilter)) return false;
      if (!q) return true;
      return [p.title, p.title_zh, p.authors.join(" "), (p.affiliations || []).join(" "), p.method, p.result, p.platform].join(" ").toLowerCase().includes(q);
    });
    const key = { relevance: (p) => -(p.relevance || 0) * 100 - (p.score || 0), score: (p) => -(p.score || 0), id: (p) => p.arxiv_id };
    list.sort((a, b) => { const ka = key[state.sort](a), kb = key[state.sort](b); return ka < kb ? -1 : ka > kb ? 1 : 0; });
    if (state.showAll) list.sort((a, b) => (b.matched || b.semantic) - (a.matched || a.semantic));
    $("#d-list").innerHTML = list.length ? list.map((p, i) => paperCard(p, i + 1)).join("")
      : `<div class="empty"><div class="big">🗞️</div>${state.digestDate ? "没有符合筛选条件的论文" : "还没有数据，点击「立即抓取最新」开始第一次汇总"}</div>`;
    bindPaperActions($("#d-list"), papers, render);
  };
  render();

  $("#d-date") && ($("#d-date").onchange = (e) => { state.digestDate = e.target.value; state.tagFilter = null; views.digest(); });
  $("#d-run").onclick = () => runJob("arxiv");
  $("#d-force").onclick = () => state.digestDate && confirm(`按当前关键词重新匹配 ${state.digestDate} 的论文，并重新总结所有相关论文？`) && runJob("arxiv", { date: state.digestDate, force: true });
  $("#d-md").onclick = () => state.digestDate && window.open(`/api/export/arxiv?date=${state.digestDate}&fmt=md`);
  $("#d-html").onclick = () => state.digestDate && window.open(`/api/export/arxiv?date=${state.digestDate}&fmt=html`);
  $("#d-push").onclick = async () => {
    if (!state.digestDate) return;
    const r = await api("/api/notify/digest", { method: "POST", body: { date: state.digestDate } });
    toast(r.ok ? "已按通知设置推送" : "推送失败：" + r.errors.join("；"), !r.ok);
  };
  $("#d-back").onclick = async () => {
    openModal('<h2>📅 回溯 arXiv 公告日</h2><div class="empty"><span class="spin">⏳</span> 读取 recent 页…</div>');
    try {
      const days = await api("/api/arxiv/available-dates");
      const have = new Set(dates.map((d) => d.date));
      $("#modal-box").innerHTML = `<h2>📅 回溯 arXiv 公告日</h2><p class="subtitle">arXiv recent 页保留最近 5 个公告日，选择一天进行汇总：</p>
        ${days.map((d) => `<div class="toolbar"><b style="width:120px">${d.date}</b><span class="kw">${d.count} 篇</span><span class="spacer"></span>
          ${have.has(d.date) ? '<span class="tag plat">已汇总</span>' : ""}<button data-date="${d.date}">${have.has(d.date) ? "补充处理" : "汇总这一天"}</button></div>`).join("")}
        <div class="modal-foot"><button onclick="closeModal()">关闭</button></div>`;
      $$("#modal-box [data-date]").forEach((b) => (b.onclick = () => { state.digestDate = b.dataset.date; closeModal(); runJob("arxiv", { date: b.dataset.date }); }));
    } catch (e) { $("#modal-box").innerHTML = `<h2>读取失败</h2><p>${esc(e.message)}</p><div class="modal-foot"><button onclick="closeModal()">关闭</button></div>`; }
  };
  if (!state.digestDate) return;
  $("#d-q").oninput = (e) => { state.q = e.target.value; render(); };
  $("#d-sort").onchange = (e) => { state.sort = e.target.value; render(); };
  $("#d-all").onchange = (e) => { state.showAll = e.target.checked; views.digest(); };
  $$("#d-tags .chip").forEach((c) => (c.onclick = () => { state.tagFilter = c.dataset.tag || null; $$("#d-tags .chip").forEach((x) => x.classList.toggle("on", x === c)); render(); }));
};

/* ================= 圈内动态 ================= */
const TYPE_INFO = { video: ["🎬", "视频"], repo: ["📦", "新仓库"], release: ["🚀", "发布"], commit: ["🛠", "提交"], post: ["📝", "博客/新闻"], link: ["🔗", "网页更新"], paper: ["📄", "论文"] };

views.feed = async () => {
  const v = $("#view");
  const f = state.feed;
  const [entities, items] = await Promise.all([api("/api/entities"),
    api("/api/feed?" + new URLSearchParams({ days: f.days, unread: f.unread ? 1 : 0, baseline: f.baseline ? 1 : 0, ...(f.entity ? { entity_id: f.entity } : {}), ...(f.type ? { type: f.type } : {}) }))]);
  const qs = items;
  const groups = {};
  qs.forEach((it) => { const d = (it.published || it.fetched_at || "").slice(0, 10); (groups[d] = groups[d] || []).push(it); });
  v.innerHTML = `
    <h1 class="title">📺 圈内动态</h1>
    <div class="subtitle">YouTube 频道、GitHub、公司新闻/博客、研究者主页与 arXiv 作者的最新工作，按设定时间自动更新</div>
    <div class="toolbar">
      <select id="f-ent"><option value="">全部关注对象</option>${entities.map((e) => `<option value="${e.id}" ${String(e.id) === String(f.entity) ? "selected" : ""}>${esc(e.name)}</option>`).join("")}</select>
      <select id="f-days">${[1, 3, 7, 14, 30, 90].map((d) => `<option value="${d}" ${d === f.days ? "selected" : ""}>最近 ${d} 天</option>`).join("")}</select>
      <label class="check"><input type="checkbox" id="f-unread" ${f.unread ? "checked" : ""}> 只看未读</label>
      <label class="check" title="新加入的网页源首次抓取时记录的已有内容"><input type="checkbox" id="f-base" ${f.baseline ? "checked" : ""}> 含主页现有内容</label>
      <span class="spacer"></span>
      <button id="f-readall">✔ 全部标为已读</button>
      <button id="f-md">⬇ 今日动态 MD</button>
      <button class="primary" id="f-run">▶ 立即抓取</button>
    </div>
    <div class="toolbar chips" id="f-types">
      <span class="chip ${!f.type ? "on" : ""}" data-type="">全部类型</span>
      ${Object.entries(TYPE_INFO).map(([k, [ic, name]]) => `<span class="chip ${f.type === k ? "on" : ""}" data-type="${k}">${ic} ${name}</span>`).join("")}
    </div>
    <div id="f-list">${qs.length ? Object.entries(groups).map(([d, its]) => `<div class="day">${d || "未知日期"}</div>${its.map(itemCard).join("")}`).join("")
      : '<div class="empty"><div class="big">📭</div>暂无动态。点击「立即抓取」或在「关注列表」里添加公司 / 研究者</div>'}</div>`;
  $("#f-ent").onchange = (e) => { f.entity = e.target.value; views.feed(); };
  $("#f-days").onchange = (e) => { f.days = +e.target.value; views.feed(); };
  $("#f-unread").onchange = (e) => { f.unread = e.target.checked; views.feed(); };
  $("#f-base").onchange = (e) => { f.baseline = e.target.checked; views.feed(); };
  $$("#f-types .chip").forEach((c) => (c.onclick = () => { f.type = c.dataset.type; views.feed(); }));
  $("#f-run").onclick = () => runJob("watch", f.entity ? { entity_id: +f.entity } : {});
  $("#f-md").onclick = () => window.open("/api/export/watch?days=1");
  $("#f-readall").onclick = async () => { await api("/api/items/read-all", { method: "POST" }); refreshBadge(); views.feed(); };
  bindItemActions($("#f-list"), qs, () => views.feed());
};

function itemCard(it) {
  const [ic, tname] = TYPE_INFO[it.type] || ["•", it.type];
  const media = it.thumbnail ? `<a href="${esc(it.url)}" target="_blank" data-act="open"><img class="thumb" loading="lazy" src="${esc(it.thumbnail)}"></a>` : `<div class="icon">${ic}</div>`;
  return `<div class="item ${it.read ? "read" : ""}" data-id="${it.id}">${media}
    <div class="body">
      <div class="meta"><span class="ent">${esc(it.entity)}</span><span>${ic} ${tname}</span><span>${timeAgo(it.published)}</span>
        ${it.baseline ? '<span class="tag grey">首次抓取</span>' : ""}${!it.read && !it.baseline ? '<span class="tag err">NEW</span>' : ""}</div>
      <a class="it" href="${esc(it.url)}" target="_blank" data-act="open">${esc(it.title)}</a>
      ${it.summary_zh ? `<div class="zh">💡 ${esc(it.summary_zh)}</div>` : ""}
      ${it.description ? `<div class="desc">${esc(it.description)}</div>` : ""}
    </div>
    <div class="acts"><button class="ghost" data-act="star">${it.starred ? "⭐" : "☆"}</button><button class="ghost" data-act="read">${it.read ? "✅" : "◻️"}</button></div>
  </div>`;
}

function bindItemActions(root, list, rerender) {
  root.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-act]");
    if (!btn) return;
    const card = btn.closest(".item");
    if (!card) return;
    const it = list.find((x) => x.id === card.dataset.id);
    const act = btn.dataset.act;
    if (act === "open") {
      if (!it.read) { it.read = 1; card.classList.add("read"); api(`/api/items/${it.id}/flag`, { method: "POST", body: { field: "read", value: 1 } }).then(refreshBadge); }
      return;
    }
    const field = act === "star" ? "starred" : "read";
    it[field] = it[field] ? 0 : 1;
    await api(`/api/items/${it.id}/flag`, { method: "POST", body: { field, value: it[field] } });
    refreshBadge();
    rerender();
  });
}

/* ================= 收藏 ================= */
views.starred = async () => {
  const [papers, items] = await Promise.all([api("/api/papers/starred"), api("/api/feed?days=0&starred=1")]);
  const v = $("#view");
  v.innerHTML = `<h1 class="title">⭐ 我的收藏</h1><div class="subtitle">收藏的论文和动态都在这里，长期保留</div>
    <div class="toolbar"><input class="search" id="s-q" type="text" placeholder="🔍 在全部历史论文中搜索（标题 / 作者 / 中文标题）"><button id="s-go">搜索</button></div>
    <div id="s-search"></div>
    <h3>论文（${papers.length}）</h3><div id="s-papers">${papers.map((p, i) => paperCard(p, i + 1, { showDate: true })).join("") || '<div class="empty">还没有收藏的论文</div>'}</div>
    <h3>动态（${items.length}）</h3><div id="s-items">${items.map(itemCard).join("") || '<div class="empty">还没有收藏的动态</div>'}</div>`;
  bindPaperActions($("#s-papers"), papers, () => views.starred());
  bindItemActions($("#s-items"), items, () => views.starred());
  const search = async () => {
    const q = $("#s-q").value.trim();
    if (!q) { $("#s-search").innerHTML = ""; return; }
    const res = await api(`/api/papers/search?q=${encodeURIComponent(q)}`);
    $("#s-search").innerHTML = `<h3>搜索结果（${res.length}）</h3>` + res.map((p, i) => paperCard(p, i + 1, { showDate: true })).join("");
    bindPaperActions($("#s-search"), res, search);
  };
  $("#s-go").onclick = search;
  $("#s-q").onkeydown = (e) => e.key === "Enter" && search();
};

/* ================= 关注列表 ================= */
let SOURCE_TYPES = {};
const KIND = { company: "🏢 公司", researcher: "🧑‍🔬 研究者", lab: "🏫 实验室", other: "📌 其它" };

views.entities = async () => {
  if (!Object.keys(SOURCE_TYPES).length) SOURCE_TYPES = await api("/api/source-types");
  const ents = await api("/api/entities");
  const v = $("#view");
  v.innerHTML = `<h1 class="title">👥 关注列表</h1>
    <div class="subtitle">关注的公司、研究者、实验室。每个对象可添加多个网址：YouTube 频道、GitHub 用户/仓库、新闻/博客页、个人主页、RSS、arXiv 作者</div>
    <div class="toolbar"><button class="primary" id="e-add">＋ 添加关注</button><span class="spacer"></span><button id="e-run">▶ 全部立即抓取</button></div>
    <div class="grid">${ents.map(entCard).join("")}</div>`;
  $("#e-add").onclick = () => editEntity(null);
  $("#e-run").onclick = () => runJob("watch");
  v.querySelector(".grid").onclick = async (e) => {
    const btn = e.target.closest("[data-act]");
    if (!btn) return;
    const ent = ents.find((x) => x.id === +btn.closest(".ent-card").dataset.id);
    const act = btn.dataset.act;
    if (act === "edit") editEntity(ent);
    if (act === "run") runJob("watch", { entity_id: ent.id });
    if (act === "view") { state.feed.entity = String(ent.id); state.feed.days = 90; go("feed"); }
    if (act === "toggle") { await api(`/api/entities/${ent.id}`, { method: "PUT", body: { ...ent, enabled: !ent.enabled } }); views.entities(); }
    if (act === "del" && confirm(`删除「${ent.name}」及其所有动态？`)) { await api(`/api/entities/${ent.id}`, { method: "DELETE" }); views.entities(); }
  };
};

function entCard(e) {
  return `<div class="ent-card ${e.enabled ? "" : "off"}" data-id="${e.id}">
    <div class="name">${esc(e.name)} <span class="tag grey">${KIND[e.kind] || e.kind}</span></div>
    <div class="note">${esc(e.note || "")}${e.filter_keywords.length ? ` <span class="tag warn" title="只保留包含这些词的动态">过滤：${e.filter_keywords.map(esc).join(", ")}</span>` : ""}</div>
    ${e.sources.map((s) => `<div class="src" title="${esc(s.last_error || s.last_status || "尚未抓取")}"><span class="dot ${s.last_status || ""}"></span>
      <span class="tag grey">${esc((SOURCE_TYPES[s.type] || s.type).split("（")[0])}</span><a class="u" href="${s.url.startsWith("http") ? esc(s.url) : "#"}" target="_blank">${esc(s.url)}</a></div>`).join("")}
    <div class="foot">
      <button data-act="view">📺 动态 ${e.item_count}</button><button data-act="run">▶ 抓取</button><button data-act="edit">✏ 编辑</button>
      <button data-act="toggle">${e.enabled ? "⏸ 停用" : "▶ 启用"}</button><button class="danger" data-act="del">🗑</button>
    </div></div>`;
}

function editEntity(ent) {
  const e = ent || { name: "", kind: "company", note: "", filter_keywords: [], enabled: 1, sources: [{ url: "" }] };
  const srcRow = (s) => `<div class="src-edit" data-sid="${s.id || ""}">
      <input type="text" class="s-url" placeholder="https://www.youtube.com/@xxx 或 github.com/xxx 或 arxiv-author:Name" value="${esc(s.url)}">
      <span class="type">${esc(SOURCE_TYPES[s.type] || "")}</span>
      <button class="s-test" title="试抓预览">🔍 试抓</button><button class="ghost s-del" title="删除">✕</button></div><div class="preview hidden"></div>`;
  openModal(`<h2>${ent ? "编辑关注" : "添加关注"}</h2>
    <div class="form-row"><label>名称</label><input type="text" id="m-name" value="${esc(e.name)}" placeholder="如：Figure AI / 廖恰源"></div>
    <div class="form-row"><label>类型</label><select id="m-kind">${Object.entries(KIND).map(([k, n]) => `<option value="${k}" ${k === e.kind ? "selected" : ""}>${n}</option>`).join("")}</select></div>
    <div class="form-row"><label>备注</label><input type="text" id="m-note" value="${esc(e.note)}" placeholder="可选"></div>
    <div class="form-row"><label>过滤关键词</label><div><input type="text" id="m-kw" style="width:100%" value="${esc(e.filter_keywords.join(", "))}" placeholder="可选，逗号分隔；如 robot, humanoid, optimus">
      <div class="hint">综合性公司（OpenAI、Tesla）建议填写，只保留与机器人相关的动态</div></div></div>
    <div class="form-row" style="align-items:start"><label>网址 / 来源</label><div>
      <div id="m-srcs">${e.sources.map(srcRow).join("")}</div>
      <button id="m-addsrc">＋ 添加网址</button>
      <div class="hint" style="margin-top:8px">支持：YouTube 频道（@handle / channel 链接）· GitHub 用户或组织（新仓库）· GitHub 仓库（Release）· 博客 / 新闻页 / 个人主页（自动找 RSS，否则检测新增链接）· 任意 RSS/Atom（含 RSSHub 的 B 站 / X 订阅）· arXiv 作者：<code>arxiv-author:Qiayuan Liao</code>，可限定分类 <code>arxiv-author:Chong Zhang|cs.RO</code></div>
    </div></div>
    <div class="modal-foot"><button onclick="closeModal()">取消</button><button class="primary" id="m-save">保存</button></div>`);
  const box = $("#modal-box");
  const detect = async (row) => {
    const url = $(".s-url", row).value.trim();
    $(".type", row).textContent = url ? (await api("/api/sources/detect", { method: "POST", body: { url } })).label : "";
  };
  const bindRow = (row) => {
    $(".s-url", row).onchange = () => detect(row);
    $(".s-del", row).onclick = () => { row.nextElementSibling.remove(); row.remove(); };
    $(".s-test", row).onclick = async () => {
      const pv = row.nextElementSibling;
      pv.classList.remove("hidden");
      pv.innerHTML = '<span class="spin">⏳</span> 抓取中…';
      const r = await api("/api/sources/test", { method: "POST", body: { url: $(".s-url", row).value.trim() } });
      pv.innerHTML = r.ok ? `✅ ${esc(SOURCE_TYPES[r.type])}：获取到 ${r.count} 条<ul>${r.items.map((i) => `<li><a href="${esc(i.url)}" target="_blank">${esc(i.title)}</a> <span class="kw">${timeAgo(i.published)}</span></li>`).join("")}</ul>`
        : `❌ ${esc(r.error)}`;
    };
  };
  $$(".src-edit", box).forEach(bindRow);
  $("#m-addsrc").onclick = () => {
    $("#m-srcs").insertAdjacentHTML("beforeend", srcRow({ url: "" }));
    const rows = $$(".src-edit", box);
    bindRow(rows[rows.length - 1]);
  };
  $("#m-save").onclick = async () => {
    const body = {
      name: $("#m-name").value, kind: $("#m-kind").value, note: $("#m-note").value, enabled: e.enabled,
      filter_keywords: $("#m-kw").value.split(/[,，]/).map((s) => s.trim()).filter(Boolean),
      sources: $$(".src-edit", box).map((r) => ({ id: r.dataset.sid ? +r.dataset.sid : undefined, url: $(".s-url", r).value.trim() })).filter((s) => s.url),
    };
    try {
      const saved = await api(ent ? `/api/entities/${ent.id}` : "/api/entities", { method: ent ? "PUT" : "POST", body });
      closeModal();
      toast("已保存");
      if (!ent) runJob("watch", { entity_id: saved.id });
      views.entities();
    } catch (err) { toast(err.message, true); }
  };
}

/* ================= 关键词主题 ================= */
views.topics = async () => {
  const s = await api("/api/settings");
  state.settings = s;
  const v = $("#view");
  const topicBox = (t, i) => `<div class="topic" data-i="${i}">
    <div class="top"><input type="text" class="t-name" value="${esc(t.name)}" placeholder="标签名（中文）">
      <div class="seg"><button class="t-role ${t.role !== "tag" ? "on" : ""}" data-role="filter" title="命中即收录到日报">筛选 + 标签</button><button class="t-role ${t.role === "tag" ? "on" : ""}" data-role="tag" title="只用于打标签，不单独决定是否收录">仅标签</button></div>
      <label class="check"><input type="checkbox" class="t-en" ${t.enabled !== false ? "checked" : ""}> 启用</label>
      <span class="spacer" style="flex:1"></span><button class="ghost danger t-del">🗑 删除</button></div>
    <textarea class="t-kw" placeholder="每行一个英文关键词；a + b 表示两个词都要出现">${esc((t.keywords || []).join("\n"))}</textarea></div>`;
  v.innerHTML = `<h1 class="title">🏷️ 关键词主题</h1>
    <div class="subtitle">每个主题 = 一个中文标签 + 一组英文关键词（匹配标题和摘要，不区分大小写和连字符）。「筛选」主题命中即收录，「仅标签」主题只用来打标签。</div>
    <div class="toolbar"><button id="t-add">＋ 添加主题</button><span class="spacer"></span>
      <label class="check" title="让 LLM 从没命中关键词的论文里补选相关论文（多一次 LLM 调用）"><input type="checkbox" id="t-sem" ${s.arxiv.semantic_screen ? "checked" : ""}> LLM 语义补筛</label>
      <button class="primary" id="t-save">💾 保存</button><button id="t-rematch">💾 保存并重新匹配最近一天</button></div>
    <div id="t-list">${s.topics.map(topicBox).join("")}</div>`;
  const collect = () => $$(".topic", v).map((el) => ({
    name: $(".t-name", el).value.trim(), role: $(".t-role.on", el).dataset.role, enabled: $(".t-en", el).checked,
    keywords: $(".t-kw", el).value.split("\n").map((x) => x.trim()).filter(Boolean),
  })).filter((t) => t.name);
  v.onclick = (e) => {
    if (e.target.classList.contains("t-role")) { $$(".t-role", e.target.parentElement).forEach((b) => b.classList.toggle("on", b === e.target)); }
    if (e.target.classList.contains("t-del")) e.target.closest(".topic").remove();
  };
  $("#t-add").onclick = () => $("#t-list").insertAdjacentHTML("afterbegin", topicBox({ name: "", role: "filter", enabled: true, keywords: [] }, -1));
  const save = async () => {
    const ns = { ...state.settings, topics: collect(), arxiv: { ...state.settings.arxiv, semantic_screen: $("#t-sem").checked } };
    state.settings = await api("/api/settings", { method: "PUT", body: ns });
    toast("主题已保存，下次汇总生效");
  };
  $("#t-save").onclick = save;
  $("#t-rematch").onclick = async () => {
    await save();
    const [dates, settings] = await Promise.all([api("/api/digest/dates"), state.settings ? state.settings : api("/api/settings")]);
  state.settings = settings;
  const topicNames = new Set(settings.topics.filter((t) => t.enabled !== false).map((t) => t.name));
    if (dates.length) { state.digestDate = dates[0].date; runJob("arxiv", { date: dates[0].date, force: true }); }
  };
};

/* ================= 设置 ================= */
views.settings = async () => {
  const s = await api("/api/settings");
  state.settings = s;
  const v = $("#view");
  const times = (id, list) => `<div class="times" id="${id}">${list.map((t) => `<span class="time-pill"><input type="time" value="${t}"><button title="删除">✕</button></span>`).join("")}<button class="add-time">＋ 添加时间</button></div>`;
  const L = s.llm, N = s.notify;
  v.innerHTML = `<h1 class="title">⚙️ 定时与设置</h1><div class="subtitle">保存后立即生效；需要保持本程序运行（建议安装为开机自启服务，见 README）</div>
  <div class="panel"><h3>📄 arXiv 论文日报</h3>
    <div class="form-row"><label>启用每日汇总</label><label class="check"><input type="checkbox" id="a-en" ${s.arxiv.enabled ? "checked" : ""}> 每天定时抓取</label></div>
    <div class="form-row"><label>运行时间</label><div>${times("a-times", s.arxiv.times)}<div class="hint">arXiv 约在北京时间 08:00（夏令时）/ 09:00（冬令时）发布新公告，建议设在其后</div></div></div>
    <div class="form-row"><label>arXiv 分类</label><div><input type="text" id="a-cats" value="${esc(s.arxiv.categories.join(", "))}" style="width:100%"><div class="hint">逗号分隔，例如 cs.RO, cs.CV, cs.LG, eess.SY（第一个用于回溯日期）</div></div></div>
    <div class="form-row"><label>选项</label><div class="chips">
      <label class="check"><input type="checkbox" id="a-cross" ${s.arxiv.include_cross_lists ? "checked" : ""}> 包含跨领域投稿（cross-list）</label>
      <label class="check"><input type="checkbox" id="a-full" ${s.arxiv.fetch_fulltext ? "checked" : ""}> 解析 PDF 首页（单位 / 通讯作者）</label>
      <label class="check"><input type="checkbox" id="a-sem" ${s.arxiv.semantic_screen ? "checked" : ""}> LLM 语义补筛</label></div></div>
    <div class="form-row"><label>每天最多总结</label><div><input type="number" id="a-max" value="${s.arxiv.max_llm_papers}" min="1" max="200"> 篇</div></div>
  </div>
  <div class="panel"><h3>📺 关注列表抓取</h3>
    <div class="form-row"><label>启用定时抓取</label><label class="check"><input type="checkbox" id="w-en" ${s.watch.enabled ? "checked" : ""}> 每天在以下时间抓取</label></div>
    <div class="form-row"><label>运行时间</label>${times("w-times", s.watch.times)}</div>
    <div class="form-row"><label>只收录最近</label><div><input type="number" id="w-days" value="${s.watch.lookback_days}" min="1" max="365"> 天内发布的内容</div></div>
    <div class="form-row"><label>中文摘要</label><label class="check"><input type="checkbox" id="w-llm" ${s.watch.llm_summarize ? "checked" : ""}> 用 LLM 为新动态生成一句话中文摘要</label></div>
  </div>
  <div class="panel"><h3>🧠 总结模型（LLM）</h3>
    <div class="form-row"><label>后端</label><select id="l-backend">
      <option value="claude_cli">本机 Claude Code（claude 命令，无需 API Key）</option>
      <option value="anthropic">Anthropic API（Claude）</option>
      <option value="openai">OpenAI 兼容接口（DeepSeek / Qwen / Kimi / OpenAI 等）</option>
      <option value="none">不使用 LLM（仅规则提取，无中文总结）</option></select></div>
    <div class="l-sec" data-b="claude_cli"><div class="form-row"><label>模型</label><div><input type="text" id="l-cli-model" value="${esc(L.claude_cli_model)}"><span class="hint"> sonnet / opus / haiku 或完整模型 ID</span></div></div></div>
    <div class="l-sec" data-b="anthropic">
      <div class="form-row"><label>模型</label><input type="text" id="l-a-model" value="${esc(L.anthropic_model)}"></div>
      <div class="form-row"><label>API Key</label><div><input type="password" id="l-a-key" value="${esc(L.anthropic_api_key)}" style="width:100%" placeholder="留空则使用环境变量 ANTHROPIC_API_KEY"></div></div></div>
    <div class="l-sec" data-b="openai">
      <div class="form-row"><label>Base URL</label><input type="text" id="l-o-url" value="${esc(L.openai_base_url)}"></div>
      <div class="form-row"><label>模型</label><input type="text" id="l-o-model" value="${esc(L.openai_model)}"></div>
      <div class="form-row"><label>API Key</label><input type="password" id="l-o-key" value="${esc(L.openai_api_key)}" style="width:100%"></div></div>
    <div class="form-row"><label>并发数</label><input type="number" id="l-conc" value="${L.concurrency}" min="1" max="16"></div>
    <div class="form-row"><label></label><div><button id="l-test">🔌 测试连接</button> <span id="l-res" class="hint"></span></div></div>
  </div>
  <div class="panel"><h3>🔔 推送通知</h3>
    <div class="form-row"><label>桌面通知</label><label class="check"><input type="checkbox" id="n-desk" ${N.desktop ? "checked" : ""}> 定时任务有新内容时弹出系统通知</label></div>
    <div class="form-row"><label>群机器人 Webhook</label><div class="times"><select id="n-type">${["none:不推送", "feishu:飞书", "dingtalk:钉钉", "wecom:企业微信", "slack:Slack", "generic:通用 JSON"].map((x) => { const [k, n] = x.split(":"); return `<option value="${k}" ${N.webhook_type === k ? "selected" : ""}>${n}</option>`; }).join("")}</select>
      <input type="text" id="n-url" value="${esc(N.webhook_url)}" placeholder="Webhook 地址" style="flex:1;min-width:320px"></div></div>
    <div class="form-row"><label>邮件</label><label class="check"><input type="checkbox" id="n-mail" ${N.email_enabled ? "checked" : ""}> 发送 HTML 日报邮件</label></div>
    <div class="form-row"><label>SMTP</label><div class="times"><input type="text" id="n-host" value="${esc(N.smtp_host)}" placeholder="smtp.qq.com"><input type="number" id="n-port" value="${N.smtp_port}" style="width:90px">
      <input type="text" id="n-user" value="${esc(N.smtp_user)}" placeholder="发件邮箱"><input type="password" id="n-pass" value="${esc(N.smtp_password)}" placeholder="授权码"></div></div>
    <div class="form-row"><label>收件人</label><input type="text" id="n-to" value="${esc(N.email_to)}" placeholder="多个用逗号分隔" style="width:100%"></div>
    <div class="form-row"><label></label><div><button id="n-test">📨 发送测试通知</button> <span class="hint">（先保存再测试）</span></div></div>
  </div>
  <div class="panel"><h3>🌐 网络</h3>
    <div class="form-row"><label>HTTP 代理</label><div><input type="text" id="x-proxy" value="${esc(s.network.proxy)}" placeholder="如 http://127.0.0.1:7897，留空直连" style="width:100%"><div class="hint">访问 YouTube / arXiv 不稳定时填写</div></div></div>
    <div class="form-row"><label>GitHub Token</label><div><input type="password" id="x-gh" value="${esc(s.network.github_token)}" style="width:100%" placeholder="可选，提高 GitHub API 限额（60→5000 次/小时）"></div></div>
  </div>
  <div class="toolbar"><span class="spacer"></span><button class="primary" id="x-save">💾 保存设置</button></div>`;

  $("#l-backend").value = L.backend;
  const showSec = () => $$(".l-sec", v).forEach((el) => el.classList.toggle("hidden", el.dataset.b !== $("#l-backend").value));
  $("#l-backend").onchange = showSec;
  showSec();
  v.addEventListener("click", (e) => {
    if (e.target.classList.contains("add-time")) e.target.insertAdjacentHTML("beforebegin", '<span class="time-pill"><input type="time" value="12:00"><button title="删除">✕</button></span>');
    else if (e.target.closest(".time-pill") && e.target.tagName === "BUTTON") e.target.closest(".time-pill").remove();
  });
  const getTimes = (id) => [...new Set($$(`#${id} input`).map((i) => i.value).filter(Boolean))].sort();
  const collect = () => ({
    ...s,
    arxiv: { ...s.arxiv, enabled: $("#a-en").checked, times: getTimes("a-times"), categories: $("#a-cats").value.split(/[,，\s]+/).filter(Boolean),
      include_cross_lists: $("#a-cross").checked, fetch_fulltext: $("#a-full").checked, semantic_screen: $("#a-sem").checked, max_llm_papers: +$("#a-max").value },
    watch: { ...s.watch, enabled: $("#w-en").checked, times: getTimes("w-times"), lookback_days: +$("#w-days").value, llm_summarize: $("#w-llm").checked },
    llm: { ...s.llm, backend: $("#l-backend").value, claude_cli_model: $("#l-cli-model").value, anthropic_model: $("#l-a-model").value,
      anthropic_api_key: $("#l-a-key").value, openai_base_url: $("#l-o-url").value, openai_model: $("#l-o-model").value,
      openai_api_key: $("#l-o-key").value, concurrency: +$("#l-conc").value },
    notify: { ...s.notify, desktop: $("#n-desk").checked, webhook_type: $("#n-type").value, webhook_url: $("#n-url").value, email_enabled: $("#n-mail").checked,
      smtp_host: $("#n-host").value, smtp_port: +$("#n-port").value, smtp_user: $("#n-user").value, smtp_password: $("#n-pass").value, email_to: $("#n-to").value },
    network: { proxy: $("#x-proxy").value.trim(), github_token: $("#x-gh").value.trim() },
  });
  const save = async () => { state.settings = await api("/api/settings", { method: "PUT", body: collect() }); toast("设置已保存，定时任务已更新"); pollStatus(); };
  $("#x-save").onclick = () => save().catch((e) => toast(e.message, true));
  $("#l-test").onclick = async () => {
    try { await save(); } catch (e) { return toast(e.message, true); }
    $("#l-res").textContent = "测试中…";
    const r = await api("/api/llm/test", { method: "POST" });
    $("#l-res").textContent = (r.ok ? "✅ " : "❌ ") + r.message;
  };
  $("#n-test").onclick = async () => {
    try { await save(); } catch (e) { return toast(e.message, true); }
    const r = await api("/api/notify/test", { method: "POST" });
    toast(r.ok ? "测试通知已发送" : "失败：" + r.errors.join("；"), !r.ok);
  };
};

/* ================= 运行记录 ================= */
views.runs = async () => {
  const runs = await api("/api/runs");
  const ST = { ok: "✅ 成功", error: "❌ 失败", partial: "⚠️ 部分失败", running: "⏳ 运行中" };
  const TR = { schedule: "定时", manual: "手动", "manual-notify": "手动", catchup: "补跑", cli: "命令行" };
  $("#view").innerHTML = `<h1 class="title">🧾 运行记录</h1><div class="subtitle">最近 50 次任务</div>
    <table class="runs"><tr><th>任务</th><th>触发</th><th>开始</th><th>状态</th><th>结果</th></tr>
    ${runs.map((r) => `<tr><td>${r.kind === "arxiv" ? "📄 arXiv" : "📺 关注列表"}</td><td>${TR[r.trigger] || r.trigger}</td><td>${fmtTime(r.started_at)}</td>
      <td class="st-${r.status}">${ST[r.status] || r.status}</td><td>${esc(r.message || "")}${r.stats?.errors?.length ? `<details><summary class="kw">失败的源</summary>${r.stats.errors.map((x) => `<div class="kw">${esc(x)}</div>`).join("")}</details>` : ""}</td></tr>`).join("")}
    </table>`;
};

/* ================= 启动 ================= */
window.closeModal = closeModal;
go((location.hash || "#digest").slice(1) in views ? location.hash.slice(1) || "digest" : "digest");
pollStatus();
refreshBadge();
