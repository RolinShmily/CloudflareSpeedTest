/**
 * Cloudflare Worker: 优选节点随机泛域名生成器
 * 纯黑白极简端庄质感，支持动态配置基准域名并持久化保存
 */

export default {
  async fetch(request, env, ctx) {
    // 默认基准域名：从环境变量或 Host 头获取
    const defaultHost = env.BASE_DOMAIN || request.headers.get("host") || "cf.269601.xyz";

    const html = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>优选节点域名生成器</title>
  <style>
    :root {
      --bg: #09090b;
      --card-bg: rgba(18, 18, 20, 0.95);
      --border: rgba(255, 255, 255, 0.12);
      --border-hover: rgba(255, 255, 255, 0.35);
      --text: #ffffff;
      --text-secondary: #a1a1aa;
      --text-muted: #71717a;
      --btn-primary-bg: #ffffff;
      --btn-primary-text: #09090b;
      --btn-secondary-bg: #18181b;
      --btn-secondary-border: rgba(255, 255, 255, 0.15);
    }
    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    }
    body {
      background: radial-gradient(circle at 50% 15%, #18181b 0%, var(--bg) 85%);
      color: var(--text);
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 24px 16px;
    }
    .container {
      width: 100%;
      max-width: 490px;
      margin: auto;
    }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      backdrop-filter: blur(20px);
      -webkit-backdrop-filter: blur(20px);
      border-radius: 20px;
      padding: 34px 28px;
      box-shadow: 0 30px 60px rgba(0, 0, 0, 0.8), inset 0 1px 0 rgba(255, 255, 255, 0.12);
      text-align: center;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      padding: 4px 14px;
      background: rgba(255, 255, 255, 0.06);
      border: 1px solid rgba(255, 255, 255, 0.15);
      border-radius: 9999px;
      font-size: 12px;
      color: #e4e4e7;
      font-weight: 500;
      margin-bottom: 18px;
      letter-spacing: 0.5px;
    }
    .badge::before {
      content: "";
      width: 5px;
      height: 5px;
      border-radius: 50%;
      background: #ffffff;
      box-shadow: 0 0 6px rgba(255, 255, 255, 0.8);
    }
    h1 {
      font-size: 22px;
      font-weight: 700;
      letter-spacing: -0.5px;
      color: #ffffff;
      margin-bottom: 6px;
    }
    p.desc {
      color: var(--text-secondary);
      font-size: 13px;
      margin-bottom: 22px;
    }

    /* 域名主展示框 */
    .domain-box {
      background: #000000;
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 20px 16px;
      cursor: pointer;
      transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
      box-shadow: 0 4px 20px rgba(0, 0, 0, 0.5);
      user-select: none;
    }
    .domain-box:hover {
      border-color: var(--border-hover);
      transform: translateY(-2px);
      box-shadow: 0 8px 30px rgba(255, 255, 255, 0.08);
    }
    .domain-box:active {
      transform: translateY(0);
    }
    .domain-display {
      font-size: 19px;
      font-weight: 600;
      letter-spacing: 0.5px;
      word-break: break-all;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-wrap: wrap;
      gap: 2px;
    }
    .prefix-highlight {
      color: #000000;
      background: #ffffff;
      padding: 2px 10px;
      border-radius: 6px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-weight: 700;
      letter-spacing: 0.3px;
      transition: all 0.25s ease;
    }
    .dot {
      color: var(--text-muted);
      margin: 0 1px;
      font-weight: 400;
    }
    .base-domain {
      color: #d4d4d8;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-weight: 500;
    }
    .copy-hint {
      margin-top: 10px;
      font-size: 11px;
      color: var(--text-muted);
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 5px;
    }

    /* 按钮组 */
    .actions {
      display: flex;
      gap: 10px;
      margin-top: 18px;
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
      gap: 7px;
      border: none;
      outline: none;
    }
    .btn-primary {
      background: var(--btn-primary-bg);
      color: var(--btn-primary-text);
      box-shadow: 0 4px 14px rgba(255, 255, 255, 0.15);
    }
    .btn-primary:hover {
      background: #f4f4f5;
      box-shadow: 0 6px 20px rgba(255, 255, 255, 0.25);
    }
    .btn-secondary {
      background: var(--btn-secondary-bg);
      color: #ffffff;
      border: 1px solid var(--btn-secondary-border);
    }
    .btn-secondary:hover {
      background: #27272a;
      border-color: rgba(255, 255, 255, 0.3);
    }
    .btn:active {
      transform: scale(0.98);
    }

    /* 备选快捷池 */
    .quick-list {
      margin-top: 22px;
      padding-top: 18px;
      border-top: 1px solid rgba(255, 255, 255, 0.08);
      text-align: left;
    }
    .quick-list-title {
      font-size: 12px;
      color: var(--text-muted);
      margin-bottom: 9px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .quick-chips {
      display: flex;
      flex-direction: column;
      gap: 7px;
    }
    .chip {
      background: #09090b;
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
      background: #18181b;
      border-color: rgba(255, 255, 255, 0.25);
    }
    .chip .chip-prefix {
      color: #ffffff;
      font-weight: 600;
    }
    .chip .chip-domain {
      color: #71717a;
    }
    .chip .chip-copy-btn {
      color: var(--text-muted);
      font-size: 11px;
      display: inline-flex;
      align-items: center;
      gap: 4px;
      transition: color 0.15s ease;
    }
    .chip:hover .chip-copy-btn {
      color: #ffffff;
    }

    /* 泛域名配置区域 */
    .config-box {
      margin-top: 20px;
      padding: 14px 16px;
      background: rgba(0, 0, 0, 0.6);
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 12px;
      text-align: left;
    }
    .config-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 12px;
      color: var(--text-secondary);
      cursor: pointer;
      user-select: none;
    }
    .config-header:hover {
      color: #ffffff;
    }
    .config-header span {
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }
    .config-body {
      margin-top: 10px;
      display: flex;
      gap: 8px;
      align-items: center;
    }
    .config-input {
      flex: 1;
      background: #18181b;
      border: 1px solid rgba(255, 255, 255, 0.15);
      border-radius: 8px;
      padding: 7px 10px;
      color: #ffffff;
      font-size: 12px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      outline: none;
      transition: border-color 0.2s;
    }
    .config-input:focus {
      border-color: #ffffff;
    }
    .config-btn {
      padding: 7px 12px;
      border-radius: 8px;
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
      border: 1px solid var(--border);
      background: #27272a;
      color: #ffffff;
      transition: all 0.2s;
      white-space: nowrap;
    }
    .config-btn:hover {
      background: #ffffff;
      color: #000000;
      border-color: #ffffff;
    }
    .config-btn-reset {
      background: transparent;
      color: var(--text-muted);
      border: 1px solid transparent;
      padding: 7px 8px;
    }
    .config-btn-reset:hover {
      background: transparent;
      color: #ffffff;
      border-color: transparent;
    }

    .info-footer {
      margin-top: 18px;
      font-size: 12px;
      color: #71717a;
      line-height: 1.6;
    }
    .info-footer code {
      color: #d4d4d8;
      background: rgba(255, 255, 255, 0.08);
      padding: 2px 5px;
      border-radius: 4px;
      font-size: 11px;
    }

    /* 黑白 Toast 提示框 */
    .toast {
      position: fixed;
      bottom: 28px;
      left: 50%;
      transform: translateX(-50%) translateY(100px);
      background: #ffffff;
      color: #000000;
      padding: 9px 18px;
      border-radius: 9999px;
      font-size: 13px;
      font-weight: 600;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.8), 0 0 15px rgba(255, 255, 255, 0.2);
      opacity: 0;
      transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
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

      <!-- 核心域名卡片 -->
      <div class="domain-box" id="domainBox" onclick="copyMainDomain()">
        <div class="domain-display">
          <span class="prefix-highlight" id="prefixText">delta63</span>
          <span class="dot">.</span>
          <span class="base-domain" id="baseDomainText">${defaultHost}</span>
        </div>
        <div class="copy-hint" id="copyHint">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
          点击快速复制完整域名
        </div>
      </div>

      <!-- 操作按钮 -->
      <div class="actions">
        <button class="btn btn-secondary" onclick="randomizeMain()">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg>
          换一个前缀
        </button>
        <button class="btn btn-primary" onclick="copyMainDomain()">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
          复制当前域名
        </button>
      </div>

      <!-- 备选池 -->
      <div class="quick-list">
        <div class="quick-list-title">
          <span>备选随机节点池 (点击直接复制)</span>
        </div>
        <div class="quick-chips" id="quickChips"></div>
      </div>

      <!-- 泛域名自定义配置区域 (可切换/持久化) -->
      <div class="config-box">
        <div class="config-header">
          <span>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg>
            泛域名设置
          </span>
          <span style="font-size: 11px; color: var(--text-muted);" id="configToggleText">自定义基准域名</span>
        </div>
        <div class="config-body" id="configBody">
          <input type="text" id="customDomainInput" class="config-input" placeholder="输入自定义基准域名 (如 cf.example.com)" />
          <button class="config-btn" onclick="saveCustomDomain()">应用</button>
          <button class="config-btn config-btn-reset" onclick="resetDefaultDomain()" title="恢复默认">重置</button>
        </div>
      </div>

      <div class="info-footer">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align: -2px; margin-right: 4px;"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>提示：任意前缀均由 <code id="wildcardTip">*.${defaultHost}</code> 泛解析自动分配至最优 Anycast 节点群，请将复制的域名粘贴至客户端的“地址 / Server”配置中。
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

    const defaultHost = "${defaultHost}";
    let currentBaseDomain = localStorage.getItem("cfst_custom_domain") || defaultHost;

    function getRandomPrefix() {
      const word = WORDS[Math.floor(Math.random() * WORDS.length)];
      const num = Math.floor(Math.random() * 90) + 10;
      return word + num;
    }

    function updateBaseDomainUI() {
      document.getElementById("baseDomainText").innerText = currentBaseDomain;
      document.getElementById("customDomainInput").value = currentBaseDomain;
      document.getElementById("wildcardTip").innerText = "*." + currentBaseDomain;
      renderChips();
    }

    function randomizeMain() {
      const prefixEl = document.getElementById("prefixText");
      prefixEl.style.transform = "scale(0.85)";
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
      const fullDomain = prefix + "." + currentBaseDomain;
      copyText(fullDomain).then(() => {
        showToast("已复制: " + fullDomain);
      });
    }

    function renderChips() {
      const container = document.getElementById("quickChips");
      container.innerHTML = "";
      for (let i = 0; i < 3; i++) {
        const prefix = getRandomPrefix();
        const full = prefix + "." + currentBaseDomain;
        const chip = document.createElement("div");
        chip.className = "chip";
        chip.innerHTML = '<span><span class="chip-prefix">' + prefix + '</span><span class="chip-domain">.' + currentBaseDomain + '</span></span><span class="chip-copy-btn"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>复制</span>';
        chip.onclick = () => {
          copyText(full).then(() => showToast("已复制: " + full));
        };
        container.appendChild(chip);
      }
    }

    function saveCustomDomain() {
      const val = document.getElementById("customDomainInput").value.trim().replace(/^\\*\\./, "").replace(/\\/.*$/, "");
      if (!val) {
        showToast("请输入有效的域名");
        return;
      }
      currentBaseDomain = val;
      localStorage.setItem("cfst_custom_domain", currentBaseDomain);
      updateBaseDomainUI();
      showToast("基准域名已更新: " + currentBaseDomain);
    }

    function resetDefaultDomain() {
      localStorage.removeItem("cfst_custom_domain");
      currentBaseDomain = defaultHost;
      updateBaseDomainUI();
      showToast("已恢复默认基准域名: " + defaultHost);
    }

    // 初始化
    updateBaseDomainUI();
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
