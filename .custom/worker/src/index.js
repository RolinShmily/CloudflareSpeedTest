/**
 * Cloudflare Worker: 优选节点随机泛域名生成器
 * 纯静态响应，零冷启动延迟，零外部依赖
 */

export default {
  async fetch(request, env, ctx) {
    // 允许通过环境变量指定固定主域名，若未指定则自动从 Host 请求头动态获取
    const host = env.BASE_DOMAIN || request.headers.get("host") || "cf.269601.xyz";

    const html = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>优选节点域名生成器</title>
  <style>
    :root {
      --bg: #090d16;
      --card-bg: rgba(22, 29, 47, 0.75);
      --border: rgba(255, 255, 255, 0.1);
      --primary: #3b82f6;
      --primary-glow: rgba(59, 130, 246, 0.35);
      --accent: #f59e0b;
      --text: #f3f4f6;
      --muted: #94a3b8;
      --success: #10b981;
    }
    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    }
    body {
      background: radial-gradient(circle at 50% 20%, #1e293b 0%, var(--bg) 80%);
      color: var(--text);
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 20px;
    }
    .container {
      width: 100%;
      max-width: 500px;
      margin: auto;
    }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      backdrop-filter: blur(16px);
      -webkit-backdrop-filter: blur(16px);
      border-radius: 20px;
      padding: 32px 24px;
      box-shadow: 0 20px 40px rgba(0, 0, 0, 0.5), inset 0 1px 0 rgba(255, 255, 255, 0.1);
      text-align: center;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 4px 12px;
      background: rgba(59, 130, 246, 0.15);
      border: 1px solid rgba(59, 130, 246, 0.3);
      border-radius: 9999px;
      font-size: 12px;
      color: #60a5fa;
      font-weight: 500;
      margin-bottom: 16px;
      letter-spacing: 0.5px;
    }
    .badge::before {
      content: "";
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: #3b82f6;
      box-shadow: 0 0 8px #3b82f6;
    }
    h1 {
      font-size: 22px;
      font-weight: 700;
      margin-bottom: 8px;
      letter-spacing: -0.5px;
    }
    p.desc {
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 24px;
      line-height: 1.5;
    }
    .domain-box {
      background: #0f172a;
      border: 1px solid rgba(59, 130, 246, 0.4);
      border-radius: 14px;
      padding: 18px 16px;
      cursor: pointer;
      transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
      position: relative;
      box-shadow: 0 4px 20px var(--primary-glow);
      user-select: none;
    }
    .domain-box:hover {
      border-color: #60a5fa;
      transform: translateY(-2px);
      box-shadow: 0 8px 24px rgba(59, 130, 246, 0.5);
    }
    .domain-box:active {
      transform: translateY(0);
    }
    .domain-display {
      font-size: 20px;
      font-weight: 600;
      letter-spacing: 0.5px;
      word-break: break-all;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-wrap: wrap;
    }
    .prefix-highlight {
      color: #38bdf8;
      background: rgba(56, 189, 248, 0.15);
      padding: 2px 8px;
      border-radius: 6px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      border: 1px dashed rgba(56, 189, 248, 0.4);
      transition: all 0.3s ease;
    }
    .dot {
      color: var(--muted);
      margin: 0 2px;
      font-weight: 400;
    }
    .base-domain {
      color: #94a3b8;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    }
    .copy-hint {
      margin-top: 8px;
      font-size: 11px;
      color: var(--muted);
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 4px;
    }
    .actions {
      display: flex;
      gap: 12px;
      margin-top: 20px;
    }
    .btn {
      flex: 1;
      padding: 12px 16px;
      border-radius: 12px;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s ease;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      border: none;
      outline: none;
    }
    .btn-primary {
      background: linear-gradient(135deg, #2563eb, #1d4ed8);
      color: #fff;
      box-shadow: 0 4px 12px rgba(37, 99, 235, 0.3);
    }
    .btn-primary:hover {
      background: linear-gradient(135deg, #3b82f6, #2563eb);
      box-shadow: 0 6px 16px rgba(37, 99, 235, 0.5);
    }
    .btn-secondary {
      background: #1e293b;
      color: var(--text);
      border: 1px solid var(--border);
    }
    .btn-secondary:hover {
      background: #334155;
      border-color: rgba(255, 255, 255, 0.2);
    }
    .btn:active {
      transform: scale(0.98);
    }
    .quick-list {
      margin-top: 24px;
      padding-top: 20px;
      border-top: 1px solid rgba(255, 255, 255, 0.08);
      text-align: left;
    }
    .quick-list-title {
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 10px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .quick-chips {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .chip {
      background: rgba(15, 23, 42, 0.6);
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 8px;
      padding: 8px 12px;
      font-size: 13px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      cursor: pointer;
      transition: all 0.15s ease;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    }
    .chip:hover {
      background: rgba(30, 41, 59, 0.8);
      border-color: rgba(56, 189, 248, 0.4);
    }
    .chip .chip-prefix {
      color: #38bdf8;
      font-weight: 600;
    }
    .chip .chip-domain {
      color: #64748b;
    }
    .chip .chip-copy-icon {
      color: var(--muted);
      font-size: 11px;
    }
    .info-footer {
      margin-top: 20px;
      font-size: 12px;
      color: #64748b;
      line-height: 1.6;
    }
    .toast {
      position: fixed;
      bottom: 24px;
      left: 50%;
      transform: translateX(-50%) translateY(100px);
      background: #10b981;
      color: #fff;
      padding: 10px 20px;
      border-radius: 9999px;
      font-size: 13px;
      font-weight: 600;
      box-shadow: 0 10px 25px rgba(16, 185, 129, 0.4);
      opacity: 0;
      transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
      pointer-events: none;
      z-index: 100;
    }
    .toast.show {
      transform: translateX(-50%) translateY(0);
      opacity: 1;
    }
  </style>
</head>
<body>
  <div class="container">
    <div class="card">
      <div class="badge">Cloudflare Anycast 优选池</div>
      <h1>优选节点域名生成器</h1>
      <p class="desc">点击下方卡片即可复制随机分配的优选节点域名</p>

      <div class="domain-box" id="domainBox" onclick="copyMainDomain()">
        <div class="domain-display">
          <span class="prefix-highlight" id="prefixText">delta63</span>
          <span class="dot">.</span>
          <span class="base-domain" id="baseDomainText">${host}</span>
        </div>
        <div class="copy-hint" id="copyHint">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
          点击快速复制完整域名
        </div>
      </div>

      <div class="actions">
        <button class="btn btn-secondary" onclick="randomizeMain()">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg>
          换一个前缀
        </button>
        <button class="btn btn-primary" onclick="copyMainDomain()">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
          复制当前域名
        </button>
      </div>

      <div class="quick-list">
        <div class="quick-list-title">
          <span>备选随机节点池 (点击直接复制)</span>
        </div>
        <div class="quick-chips" id="quickChips"></div>
      </div>

      <div class="info-footer">
        💡 提示：任意前缀均由 <code>*.${host}</code> 泛解析自动分配至最优 Anycast 节点群，请将复制的域名粘贴至客户端的“地址 / Server”配置中。
      </div>
    </div>
  </div>

  <div class="toast" id="toast">已成功复制到剪贴板！</div>

  <script>
    const WORDS = [
      "delta", "alpha", "bravo", "nova", "apex", "echo", "pulse", "solar",
      "lunar", "orbit", "swift", "sonic", "flux", "halo", "jade", "neon",
      "ruby", "wave", "zenith", "frost", "cyber", "spark", "core", "bolt",
      "iris", "mint", "tide", "kilo", "lima", "vector", "nexus", "prism",
      "atlas", "blaze", "comet", "dune", "flare", "haven", "ignite", "karma",
      "mirage", "nebula", "omega", "quantum", "radar", "shadow", "titan",
      "ultra", "vortex", "warp", "xenon", "zero", "kestrel", "laser", "phoenix"
    ];

    const baseDomain = "${host}";

    function getRandomPrefix() {
      const word = WORDS[Math.floor(Math.random() * WORDS.length)];
      const num = Math.floor(Math.random() * 90) + 10; // 两位数 10~99
      return word + num;
    }

    function randomizeMain() {
      const prefixEl = document.getElementById("prefixText");
      prefixEl.style.transform = "scale(0.8)";
      prefixEl.style.opacity = "0.5";
      setTimeout(() => {
        prefixEl.innerText = getRandomPrefix();
        prefixEl.style.transform = "scale(1)";
        prefixEl.style.opacity = "1";
      }, 100);
      renderChips();
    }

    function copyText(text) {
      if (navigator.clipboard && window.isSecureContext) {
        return navigator.clipboard.writeText(text);
      }
      const textArea = document.createElement("textarea");
      textArea.value = text;
      textArea.style.position = "fixed";
      textArea.style.left = "-999999px";
      document.body.appendChild(textArea);
      textArea.focus();
      textArea.select();
      return new Promise((resolve, reject) => {
        document.execCommand("copy") ? resolve() : reject();
        textArea.remove();
      });
    }

    function showToast(msg) {
      const toast = document.getElementById("toast");
      toast.innerText = msg;
      toast.classList.add("show");
      setTimeout(() => toast.classList.remove("show"), 2000);
    }

    function copyMainDomain() {
      const prefix = document.getElementById("prefixText").innerText;
      const fullDomain = prefix + "." + baseDomain;
      copyText(fullDomain).then(() => {
        showToast("已复制: " + fullDomain);
      });
    }

    function renderChips() {
      const container = document.getElementById("quickChips");
      container.innerHTML = "";
      for (let i = 0; i < 3; i++) {
        const prefix = getRandomPrefix();
        const full = prefix + "." + baseDomain;
        const chip = document.createElement("div");
        chip.className = "chip";
        chip.innerHTML = '<span><span class="chip-prefix">' + prefix + '</span><span class="chip-domain">.' + baseDomain + '</span></span><span class="chip-copy-icon">点击复制 📋</span>';
        chip.onclick = () => {
          copyText(full).then(() => showToast("已复制: " + full));
        };
        container.appendChild(chip);
      }
    }

    // 初始化
    renderChips();
  </script>
</body>
</html>`;

    return new Response(html, {
      headers: {
        "content-type": "text/html;charset=UTF-8",
        "cache-control": "public, max-age=60, s-maxage=3600"
      }
    });
  }
};
