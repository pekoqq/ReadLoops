// ReadLoops 阅读器
const $ = (s) => document.querySelector(s);
// 返回真数组（而不是 NodeList）：NodeList 没有 filter/map 等方法，
// 图谱页需要按类型过滤节点，用数组更省事；forEach 用法完全兼容。
const $$ = (s) => Array.from(document.querySelectorAll(s));

let currentArticle = null;
let timerInterval = null;
let timerSeconds = 0;
let timerRunning = false;
let lookupsInSession = 0;
let highlightsInSession = 0;

const api = {
  async get(url) { const r = await fetch(url); return r.json(); },
  async post(url, data) {
    const r = await fetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(data || {})
    });
    return r.json();
  },
  async delete(url) {
    const r = await fetch(url, { method: 'DELETE' });
    return r.json();
  }
};

/**
 * 应用内确认对话框（替代原生 confirm）。
 *
 * 为什么不用原生 confirm()：在 Chrome「应用模式」（--app=）与已安装的 PWA 中，
 * 原生弹窗可能被浏览器拦截或用户曾勾选「阻止此页面创建更多对话框」，
 * 此时 confirm() 会静默返回 false —— 表现为「点了删除没反应」。
 * 用应用内自绘对话框可彻底规避，且样式与应用一致。
 *
 * @returns {Promise<boolean>} 用户是否确认
 */
function confirmDialog(message, { okText = '确定', cancelText = '取消', danger = false } = {}) {
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.className = 'app-dialog-overlay';
    overlay.innerHTML =
      '<div class="app-dialog" role="dialog" aria-modal="true">' +
        '<div class="app-dialog-message">' + escapeHtml(message) + '</div>' +
        '<div class="app-dialog-actions">' +
          '<button class="toolbar-btn" data-act="cancel">' + escapeHtml(cancelText) + '</button>' +
          '<button class="toolbar-btn ' + (danger ? 'danger' : 'primary') + '" data-act="ok">' +
            escapeHtml(okText) +
          '</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);

    const okBtn = overlay.querySelector('[data-act="ok"]');
    const done = (val) => {
      overlay.remove();
      document.removeEventListener('keydown', onKey);
      resolve(val);
    };
    const onKey = (e) => {
      if (e.key === 'Escape') done(false);
      else if (e.key === 'Enter') done(true);
    };

    okBtn.addEventListener('click', () => done(true));
    overlay.querySelector('[data-act="cancel"]').addEventListener('click', () => done(false));
    overlay.addEventListener('click', (e) => { if (e.target === overlay) done(false); });
    document.addEventListener('keydown', onKey);
    okBtn.focus();
  });
}

/** 应用内提示框（替代原生 alert）。 */
function showAlert(message) {
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.className = 'app-dialog-overlay';
    overlay.innerHTML =
      '<div class="app-dialog" role="dialog" aria-modal="true">' +
        '<div class="app-dialog-message">' + escapeHtml(message) + '</div>' +
        '<div class="app-dialog-actions">' +
          '<button class="toolbar-btn primary" data-act="ok">知道了</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);

    const okBtn = overlay.querySelector('[data-act="ok"]');
    const done = () => { overlay.remove(); resolve(); };
    okBtn.addEventListener('click', done);
    overlay.addEventListener('click', (e) => { if (e.target === overlay) done(); });
    okBtn.focus();
  });
}

function toast(msg, duration = 2000) {
  const t = document.createElement('div');
  t.className = 'toast';
  t.textContent = msg;
  document.body.appendChild(t);
  requestAnimationFrame(() => t.classList.add('show'));
  setTimeout(() => {
    t.classList.remove('show');
    setTimeout(() => t.remove(), 300);
  }, duration);
}

// 主题
$('#themeSelect').addEventListener('change', (e) => {
  // 切换瞬间临时开启全局颜色过渡，让四个主题之间平滑渐变而不是硬切。
  // 过渡只在这 420ms 内生效，不会拖慢平时的交互动画。
  document.body.classList.add('theme-transition');
  document.documentElement.setAttribute('data-theme', e.target.value);
  localStorage.setItem('yuedu-theme', e.target.value);
  // 图谱的配色取自主题变量（节点/标签/连线都是 currentColor 体系），
  // 切主题后必须重绘一次，否则浅色主题下会留着上一套颜色。
  if (graphState) drawGraph();
  clearTimeout(window.__themeTransitionTimer);
  window.__themeTransitionTimer = setTimeout(() => {
    document.body.classList.remove('theme-transition');
  }, 420);
});
const savedTheme = localStorage.getItem('yuedu-theme');
if (savedTheme) {
  document.documentElement.setAttribute('data-theme', savedTheme);
  $('#themeSelect').value = savedTheme;
}

// 侧边栏
$('#toggleSidebar').addEventListener('click', () => {
  // 用户手动点击折叠按钮，退出自动侧边栏模式
  autoSidebar = false;
  const app = document.querySelector('.app');
  if (app) app.classList.remove('auto-sidebar-mode');
  const sidebar = $('#sidebar');
  sidebar.classList.remove('sidebar-visible');
  sidebar.classList.toggle('collapsed');
});

// 自动侧边栏模式：和文章互动时收起侧边栏
const readerContainer = $('#readerContainer');
function autoCollapseSidebar() {
  if (autoSidebar) {
    $('#sidebar').classList.remove('sidebar-visible');
  }
  // 文章互动自动开启专注模式
  tryAutoFocus();
}
readerContainer.addEventListener('click', autoCollapseSidebar);
readerContainer.addEventListener('scroll', autoCollapseSidebar);
document.addEventListener('mouseup', (e) => {
  // 划词结束时，如果在阅读区内，收起侧边栏
  if (autoSidebar && readerContainer.contains(e.target)) {
    const sel = window.getSelection();
    if (sel && sel.toString().trim()) {
      // 有划词，不立即收起（等查词面板操作完），但延迟收起
      setTimeout(() => {
        if (autoSidebar) $('#sidebar').classList.remove('sidebar-visible');
      }, 1500);
    } else {
      autoCollapseSidebar();
    }
  }
  // 划词也触发自动专注
  if (readerContainer.contains(e.target)) {
    tryAutoFocus();
  }
});
// 专注模式
let focusMode = false;
let focusStartTime = 0;
let focusTotalTime = 0;
let autoFocusTriggered = false; // 防止重复触发自动专注

$('#focusBtn').addEventListener('click', toggleFocusMode);

// 水纹扩散引导效果 — feTurbulence像素扭曲+涟漪环
let rippleAnim = null;

function triggerRipple() {
  const reader = document.querySelector('.reader');
  const container = $('#rippleContainer');
  const turbulence = document.getElementById('ripple-turbulence');
  const displace = document.getElementById('ripple-displace');
  if (!reader || !container || !turbulence || !displace) return;
  
  // 清除之前的动画
  if (rippleAnim) cancelAnimationFrame(rippleAnim);
  
  container.innerHTML = '';
  
  // 创建涟漪环（从顶部中心扩散）
  const ring = document.createElement('div');
  ring.className = 'ripple-wave';
  ring.style.left = '50%';
  ring.style.top = '0';
  container.appendChild(ring);
  
  // 滤镜只作用于文章区域，避免整个界面色差
  reader.style.filter = 'url(#ripple-distort)';
  
  // 3秒动画：scale从0→峰值→0，baseFrequency流动模拟水波
  const startTime = performance.now();
  const duration = 3000;
  const maxScale = 20;
  
  function animate(now) {
    const elapsed = now - startTime;
    const progress = Math.min(elapsed / duration, 1);
    
    // scale：sin曲线，最后15%更平缓地衰减到0，避免色差跳变
    let scale;
    if (progress < 0.85) {
      scale = Math.sin((progress / 0.85) * Math.PI * 0.5) * maxScale;
    } else {
      const fadeProgress = (progress - 0.85) / 0.15;
      scale = maxScale * (1 - fadeProgress) * 0.5;
    }
    displace.setAttribute('scale', scale.toString());
    
    // baseFrequency：缓慢流动，模拟水波荡漾
    const freq = 0.012 + Math.sin(progress * Math.PI * 2.5) * 0.006;
    turbulence.setAttribute('baseFrequency', freq.toString());
    
    if (progress < 1) {
      rippleAnim = requestAnimationFrame(animate);
    } else {
      // scale归零后等待一帧，再清除filter（.app已常驻合成层，无切换色差）
      displace.setAttribute('scale', '0');
      turbulence.setAttribute('baseFrequency', '0.012');
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          reader.style.filter = '';
          container.innerHTML = '';
        });
      });
      rippleAnim = null;
    }
  }
  
  rippleAnim = requestAnimationFrame(animate);
}

function toggleFocusMode() {
  // 专注模式只服务于「沉浸阅读」：非阅读页一律不允许进入。
  // 这是兜底防线——无论从按钮、快捷键还是将来的新入口触发，都拦得住。
  const view = document.querySelector('.nav-item.active')?.dataset.view;
  if (!focusMode && view && view !== 'reader') {
    toast('专注模式仅在阅读页可用');
    return;
  }
  focusMode = !focusMode;
  document.querySelector('.app').classList.toggle('focus-mode', focusMode);
  // body 同步切类：驱动氛围光层上移 / 回位（过渡与水纹涟漪对齐）
  document.body.classList.toggle('focus-mode', focusMode);
  if (focusMode) {
    focusStartTime = Date.now();
    $('#focusBtn').textContent = '退出专注';
    $('#focusBtn').classList.add('active');
    // 进入专注 = 开始沉浸阅读：有文章且计时未跑时同步开始计时
    if (currentArticle && !timerRunning) startTimer();
    toast('已进入专注模式');
    // 触发水纹扩散引导
    setTimeout(triggerRipple, 300);
  } else {
    const duration = Math.round((Date.now() - focusStartTime) / 1000);
    focusTotalTime += duration;
    $('#focusBtn').textContent = '专注';
    $('#focusBtn').classList.remove('active');
    // 退出专注 = 暂停阅读：同步暂停计时（再次进入会从当前读数继续）
    if (timerRunning) pauseTimer();
    // 已非专注，计时按钮回到「开始（即进入专注）」语义，而不是「继续」
    $('#timerBtn').textContent = '开始 [空格]';
    const mins = Math.floor(duration / 60);
    const secs = duration % 60;
    toast(`本次专注 ${mins}分${secs}秒`);
  }
}

// 文章互动自动开启专注模式
function tryAutoFocus() {
  // 只在阅读页自动进入专注。
  // 生词本 / 历史文章 / 统计页的内容同样渲染在 #readerContainer 里，
  // 在这些页面点一下也会走到这里；若不拦住，autoFocusTriggered 会被
  // 提前置为 true，回到阅读页后自动专注就再也触发不了了。
  const view = document.querySelector('.nav-item.active')?.dataset.view;
  if (view !== 'reader') return;
  if (focusMode || autoFocusTriggered || !currentArticle) return;
  autoFocusTriggered = true;
  toggleFocusMode();
}

// 专注模式下鼠标移到顶部显示工具栏
const focusTopTrigger = $('#focusTopTrigger');
let toolbarHoverTimer = null;
focusTopTrigger.addEventListener('mouseenter', () => {
  if (!focusMode) return;
  clearTimeout(toolbarHoverTimer);
  $('.toolbar').classList.add('show-on-hover');
});
focusTopTrigger.addEventListener('mouseleave', () => {
  if (!focusMode) return;
  toolbarHoverTimer = setTimeout(() => {
    $('.toolbar').classList.remove('show-on-hover');
  }, 500);
});
// 工具栏本身hover时保持显示
document.querySelector('.toolbar').addEventListener('mouseenter', () => {
  if (focusMode) {
    clearTimeout(toolbarHoverTimer);
    $('.toolbar').classList.add('show-on-hover');
  }
});
document.querySelector('.toolbar').addEventListener('mouseleave', () => {
  if (focusMode) {
    toolbarHoverTimer = setTimeout(() => {
      $('.toolbar').classList.remove('show-on-hover');
    }, 500);
  }
});

// 专注模式下鼠标移到左侧显示侧边栏
const focusLeftTrigger = $('#focusLeftTrigger');
let sidebarHoverTimer = null;
focusLeftTrigger.addEventListener('mouseenter', () => {
  if (focusMode) {
    clearTimeout(sidebarHoverTimer);
    $('#sidebar').classList.add('show-on-hover');
  } else if (autoSidebar) {
    // 自动侧边栏模式：鼠标移到左边缘，展开侧边栏
    clearTimeout(sidebarHoverTimer);
    $('#sidebar').classList.add('sidebar-visible');
  }
});
focusLeftTrigger.addEventListener('mouseleave', () => {
  if (focusMode) {
    sidebarHoverTimer = setTimeout(() => {
      $('#sidebar').classList.remove('show-on-hover');
    }, 500);
  } else if (autoSidebar) {
    // 自动侧边栏模式：鼠标离开左边缘，延迟收起
    sidebarHoverTimer = setTimeout(() => {
      if (autoSidebar) $('#sidebar').classList.remove('sidebar-visible');
    }, 300);
  }
});
$('#sidebar').addEventListener('mouseenter', () => {
  if (focusMode) {
    clearTimeout(sidebarHoverTimer);
    $('#sidebar').classList.add('show-on-hover');
  } else if (autoSidebar) {
    // 鼠标移入侧边栏，取消收起
    clearTimeout(sidebarHoverTimer);
  }
});
$('#sidebar').addEventListener('mouseleave', () => {
  if (focusMode) {
    sidebarHoverTimer = setTimeout(() => {
      $('#sidebar').classList.remove('show-on-hover');
    }, 500);
  } else if (autoSidebar) {
    // 鼠标离开侧边栏，延迟收起
    sidebarHoverTimer = setTimeout(() => {
      if (autoSidebar) $('#sidebar').classList.remove('sidebar-visible');
    }, 300);
  }
});

// 键盘快捷键
document.addEventListener('keydown', (e) => {
  // Esc 退出专注
  if (e.key === 'Escape' && focusMode) {
    toggleFocusMode();
    return;
  }
  // F 切换专注（仅在阅读页、不在输入框中时）
  if (e.key === 'f' && !focusMode && !['INPUT','TEXTAREA'].includes(e.target.tagName)) {
    const view = document.querySelector('.nav-item.active')?.dataset.view;
    if (view !== 'reader') return;   // 非阅读页不响应，也不吞掉按键
    e.preventDefault();
    toggleFocusMode();
    return;
  }
});

// 计时器
function formatTime(s) {
  const m = Math.floor(s / 60).toString().padStart(2, '0');
  const sec = (s % 60).toString().padStart(2, '0');
  return `${m}:${sec}`;
}
function startTimer() {
  // 计时器与专注模式一体：非专注状态下一律不计时（根防线，任何入口都绕不过）
  if (!focusMode) return;
  if (timerRunning) return;
  timerRunning = true;
  $('#timerBtn').textContent = '暂停 [空格]';
  timerInterval = setInterval(() => {
    timerSeconds++;
    $('#timerDisplay').textContent = formatTime(timerSeconds);
  }, 1000);
}
function pauseTimer() {
  timerRunning = false;
  clearInterval(timerInterval);
  $('#timerBtn').textContent = '继续 [空格]';
}
// 计时与专注一体：非专注时「开始计时」即进入专注模式；已在专注中则只做暂停 / 继续
function toggleTimerWithFocus() {
  if (!focusMode) {
    const view = document.querySelector('.nav-item.active')?.dataset.view;
    if (view !== 'reader' || !currentArticle) return; // 非阅读页 / 还没文章：不启动
    toggleFocusMode(); // 进入专注，其进入分支会负责 startTimer
  } else {
    timerRunning ? pauseTimer() : startTimer();
  }
}
$('#timerBtn').addEventListener('click', toggleTimerWithFocus);

document.addEventListener('keydown', (e) => {
  if (e.code === 'Space' && !['INPUT','SELECT','TEXTAREA'].includes(e.target.tagName)) {
    e.preventDefault();
    toggleTimerWithFocus();
  }
  if (e.key === 'Escape') { hideLookupPanel(); closeSettings(); }
});

// 鼠标离开窗口自动暂停计时
document.addEventListener('mouseleave', () => {
  if (timerRunning) pauseTimer();
});
// 窗口失焦也暂停
window.addEventListener('blur', () => {
  if (timerRunning) pauseTimer();
});

// 生成文章
// 生成等待话术
const GENERATING_PHRASES = [
  '正在参考历年四级真题...',
  '正在分析您的词汇掌握程度...',
  '正在提取高频短语和搭配...',
  '正在构建文章结构...',
  '正在融入目标生词...',
  '正在润色语句节奏...',
  '即将完成...',
];
let phraseInterval = null;
let typewriterTimer = null;
let typewriterActive = false;
let isGenerating = false;
let generatingActive = false;
let autoSidebar = false; // 自动侧边栏模式：生成文章后启用，互动收回，边缘展开

const sleep = ms => new Promise(r => setTimeout(r, ms));

async function runGeneratingPhrases() {
  generatingActive = true;
  let idx = 0;
  while (generatingActive) {
    const el = $('#generatingText');
    if (!el) break;
    // 淡出
    el.style.opacity = '0';
    await sleep(250);
    if (!generatingActive) break;
    // 切换文字
    idx = (idx + 1) % GENERATING_PHRASES.length;
    el.textContent = GENERATING_PHRASES[idx];
    // 淡入
    el.style.opacity = '1';
    await sleep(1100);
  }
}

function startGeneratingAnimation() {
  stopGeneratingAnimation();
  generatingActive = true;
  // 显示动态等待界面
  $('#reader').innerHTML = `
    <div class="generating-container">
      <div class="generating-spinner"></div>
      <div class="generating-text" id="generatingText">正在参考历年四级真题...</div>
      <div class="generating-subtext">ReadLoops 正在为你锻造专属阅读材料</div>
    </div>
  `;
  // 启动话术轮播（异步循环，不阻塞）
  runGeneratingPhrases();
}

function stopGeneratingAnimation() {
  generatingActive = false;
  if (phraseInterval) {
    clearInterval(phraseInterval);
    phraseInterval = null;
  }
}

// 打字机效果
function typewriterRender(article) {
  const reader = $('#reader');
  const paragraphs = article.content.split('\n').filter(p => p.trim());
  reader.innerHTML = `<h1 class="typewriter-title">${escapeHtml(article.title)}</h1>`;

  typewriterActive = true;
  let paraIdx = 0;
  let charIdx = 0;
  let currentP = null;

  function finish() {
    typewriterActive = false;
    typewriterTimer = null;
    if (currentP) currentP.classList.remove('typing');
    $('#articleMeta').textContent = `${article.word_count} 词 · ${article.new_word_count} 生词`;
    toast('文章已生成');
  }

  function typeNext() {
    if (!typewriterActive) return;

    if (paraIdx >= paragraphs.length) {
      finish();
      return;
    }

    if (!currentP || charIdx === 0) {
      if (currentP) currentP.classList.remove('typing');
      currentP = document.createElement('p');
      currentP.classList.add('typing');
      reader.appendChild(currentP);
    }

    if (charIdx < paragraphs[paraIdx].length) {
      currentP.textContent += paragraphs[paraIdx][charIdx];
      charIdx++;
      $('#readerContainer').scrollTop = $('#readerContainer').scrollHeight;
      typewriterTimer = setTimeout(typeNext, 16);
    } else {
      currentP.classList.remove('typing');
      paraIdx++;
      charIdx = 0;
      currentP = null;
      typewriterTimer = setTimeout(typeNext, 120);
    }
  }
  typeNext();
}

function stopTypewriter() {
  typewriterActive = false;
  if (typewriterTimer) {
    clearTimeout(typewriterTimer);
    typewriterTimer = null;
  }
}

$('#generateBtn').addEventListener('click', async () => {
  if (isGenerating) return;

  // 【最优先】立即收起侧边栏，在任何 await 之前，确保一定执行
  autoSidebar = true;
  document.querySelector('.app').classList.add('auto-sidebar-mode');
  $('#sidebar').classList.remove('sidebar-visible');
  $('#sidebar').classList.add('collapsed');
  // 重置自动专注触发，新文章可以再次触发
  autoFocusTriggered = false;

  // 切换到阅读视图
  $$('.nav-item').forEach(i => i.classList.remove('active'));
  document.querySelector('[data-view="reader"]').classList.add('active');
  $('#reader').classList.remove('wide-view');

  // 保存上一次阅读会话（失败不影响后续流程）
  try {
    if (currentArticle && timerSeconds > 0) {
      await api.post('/api/reading/session', {
        article_id: currentArticle.id, duration_seconds: timerSeconds,
        lookups: lookupsInSession, highlights: highlightsInSession
      });
    }
  } catch (e) { console.log('保存阅读会话失败:', e); }

  timerSeconds = 0; lookupsInSession = 0; highlightsInSession = 0;
  $('#timerDisplay').textContent = '00:00';
  pauseTimer();

  isGenerating = true;
  $('#generateStatus').innerHTML = '<span class="loading"></span> 生成中';
  $('#generateBtn').disabled = true;

  // 启动生成等待动画
  startGeneratingAnimation();

  try {
    const article = await api.post('/api/articles/generate');
    if (article.detail) throw new Error(article.detail);
    currentArticle = article;
    isGenerating = false;
    stopGeneratingAnimation();
    $('#generateStatus').textContent = '';
    $('#generateBtn').disabled = false;
    // 再次确保侧边栏收起
    document.querySelector('.app').classList.add('auto-sidebar-mode');
    $('#sidebar').classList.remove('sidebar-visible');
    // 如果当前在阅读页面，先淡出等待界面，再播放打字机
    const activeView = document.querySelector('.nav-item.active')?.dataset.view;
    if (activeView === 'reader') {
      const container = $('.generating-container');
      if (container) {
        container.style.transition = 'opacity 0.25s ease';
        container.style.opacity = '0';
        await sleep(250);
      }
      typewriterRender(article);
    }
  } catch (err) {
    isGenerating = false;
    stopGeneratingAnimation();
    $('#generateStatus').textContent = '';
    $('#generateBtn').disabled = false;
    // 生成失败，恢复侧边栏
    autoSidebar = false;
    document.querySelector('.app').classList.remove('auto-sidebar-mode');
    $('#sidebar').classList.remove('collapsed');
    const activeView = document.querySelector('.nav-item.active')?.dataset.view;
    if (activeView === 'reader') {
      $('#reader').innerHTML = `<div class="empty"><h2>生成失败</h2><p>请检查 AI 配置</p></div>`;
    }
    toast('生成失败，请检查 AI 配置');
    console.error(err);
  }
});

function renderArticle(article) {
  $('#reader').innerHTML = `<h1>${escapeHtml(article.title)}</h1>` +
    article.content.split('\n').filter(p => p.trim()).map(p => `<p>${escapeHtml(p)}</p>`).join('');
  $('#readerContainer').scrollTop = 0;
  // 恢复该文章的高亮记录
  restoreHighlights(article.id);
}

async function restoreHighlights(articleId) {
  if (!articleId) return;
  try {
    const highlights = await api.get(`/api/reading/highlights/${articleId}`);
    if (!highlights || !highlights.length) return;
    const reader = $('#reader');
    highlights.forEach(h => {
      if (!h.text) return;
      // 遍历所有文本节点，找到匹配的文本并用mark包裹
      const walker = document.createTreeWalker(reader, NodeFilter.SHOW_TEXT, null);
      let node;
      while ((node = walker.nextNode())) {
        const idx = node.nodeValue.indexOf(h.text);
        if (idx !== -1 && !node.parentElement.closest('mark')) {
          const range = document.createRange();
          range.setStart(node, idx);
          range.setEnd(node, idx + h.text.length);
          const mark = document.createElement('mark');
          try {
            range.surroundContents(mark);
          } catch(e) {}
          break;
        }
      }
    });
  } catch (err) {
    console.log('恢复高亮失败:', err);
  }
}
function escapeHtml(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

/**
 * 给成组出现的元素设置错峰延迟，避免它们「一起蹦出来」。
 * 延迟随序号递增，但有上限，防止长列表最后一项等太久。
 */
function staggerIn(selector, step = 0.045, max = 0.45) {
  $$(selector).forEach((el, i) => {
    el.style.animationDelay = Math.min(i * step, max) + 's';
  });
}

// 划词查词
const lookupPanel = $('#lookupPanel');
let currentSelection = '';

document.addEventListener('mouseup', (e) => {
  if (lookupPanel.contains(e.target)) return;
  const sel = window.getSelection();
  const text = sel.toString().trim();
  // 上限 280：整句翻译需要容纳 1~2 个完整句子（原 60 稍长一点的句子直接不弹面板）
  if (!text || text.length > 280 || !/[a-zA-Z]/.test(text)) { hideLookupPanel(); return; }
  if (!$('#reader').contains(sel.anchorNode)) { hideLookupPanel(); return; }
  currentSelection = text;
  showLookupPanel(e.clientX, e.clientY, text);
});

async function showLookupPanel(x, y, word) {
  lookupPanel.style.left = Math.min(x + 12, window.innerWidth - 320) + 'px';
  lookupPanel.style.top = Math.min(y + 12, window.innerHeight - 200) + 'px';
  lookupPanel.classList.add('show');
  $('#lpWord').textContent = word;
  $('#lpPhonetic').textContent = '';
  $('#lpMeaning').textContent = '查询中...';
  // 计时由「划词自动进入专注」统一带动（mouseup → tryAutoFocus → 进专注即计时），
  // 这里不再单独 startTimer，避免出现非专注计时

  // 选中多个单词 → 翻译整句；单个单词 → 查词典
  if (/\s/.test(word.trim())) {
    lookupPanel.classList.add('sentence-mode');
    lookupPanel.dataset.wordId = '';
    requestSentenceTranslate(word);
    return;
  }
  lookupPanel.classList.remove('sentence-mode');

  try {
    const result = await api.get(`/api/words/lookup/${encodeURIComponent(word)}`);
    $('#lpPhonetic').textContent = result.phonetic || '';
    $('#lpMeaning').textContent = result.meaning || '未找到释义';
    lookupPanel.dataset.wordId = result.word_id || '';
    if (result.word_id) {
      lookupsInSession++;
      const aid = currentArticle ? currentArticle.id : 0;
      api.post(`/api/reading/lookup?word_id=${result.word_id}&article_id=${aid}&context=${encodeURIComponent(currentSelection)}`)
        .catch(e => console.log('记录查词失败:', e));
      // 刷新右侧查词历史
      if (typeof loadLookupHistory === 'function') loadLookupHistory();
    }
  } catch (err) { $('#lpMeaning').textContent = '查询失败'; }
}
// 整句翻译：独立函数，失败时在面板内给出「点击重试」而不是一句死掉的「翻译失败」
async function requestSentenceTranslate(word) {
  const meaning = $('#lpMeaning');
  meaning.textContent = '翻译中...';
  try {
    const res = await api.post('/api/words/translate', { text: word });
    if (res && res.ok && res.translation) {
      meaning.textContent = res.translation;
      return;
    }
    renderTranslateRetry(word, res && res.error);
  } catch (err) {
    renderTranslateRetry(word, String(err));
  }
}
function renderTranslateRetry(word, reason) {
  if (reason) console.warn('整句翻译失败:', reason);
  const meaning = $('#lpMeaning');
  meaning.innerHTML = '';
  const hint = document.createElement('div');
  hint.className = 'translate-hint';
  hint.textContent = '翻译失败（网络或 AI 服务波动）';
  const btn = document.createElement('button');
  btn.className = 'translate-retry-btn';
  btn.textContent = '点击重试';
  btn.addEventListener('click', () => requestSentenceTranslate(word));
  meaning.append(hint, btn);
}
function hideLookupPanel() { lookupPanel.classList.remove('show'); }

$('#lpPronounce').addEventListener('click', () => {
  const utter = new SpeechSynthesisUtterance($('#lpWord').textContent);
  utter.lang = 'en-US'; utter.rate = 0.9;
  speechSynthesis.speak(utter);
});
$('#lpHighlight').addEventListener('click', () => {
  const sel = window.getSelection();
  if (sel.rangeCount > 0) {
    const range = sel.getRangeAt(0);
    const mark = document.createElement('mark');
    try { range.surroundContents(mark); } catch(e) {}
    highlightsInSession++;
    if (currentArticle) {
      api.post('/api/reading/highlight', {
        article_id: currentArticle.id, text: currentSelection,
        word_id: lookupPanel.dataset.wordId ? parseInt(lookupPanel.dataset.wordId) : null,
        color: 'yellow'
      });
    }
    toast('已高亮');
  }
  hideLookupPanel();
});
$('#lpAdd').addEventListener('click', async () => {
  const word = $('#lpWord').textContent;
  await api.post('/api/words/add', { word, meaning: $('#lpMeaning').textContent, context: currentSelection });
  toast(`已添加「${word}」`);
  hideLookupPanel();
});
// 点击已高亮的文本取消高亮
document.addEventListener('click', (e) => {
  const mark = e.target.closest('mark');
  if (mark && $('#reader').contains(mark)) {
    e.preventDefault();
    e.stopPropagation();
    const text = mark.textContent;
    // unwrap mark 标签，恢复普通文本
    const parent = mark.parentNode;
    while (mark.firstChild) parent.insertBefore(mark.firstChild, mark);
    parent.removeChild(mark);
    parent.normalize();
    // 从数据库删除高亮记录
    if (currentArticle) {
      api.delete(`/api/reading/highlight?article_id=${currentArticle.id}&text=${encodeURIComponent(text)}`)
        .catch(err => console.log('删除高亮失败:', err));
    }
    toast('已取消高亮');
  }
});
document.addEventListener('mousedown', (e) => {
  if (!lookupPanel.contains(e.target) && !$('#reader').contains(e.target)) hideLookupPanel();
});

// 添加生词
$('#addWordBtn').addEventListener('click', () => {
  // 专注模式下打开生词本，自动退出专注模式
  if (focusMode) toggleFocusMode();
  $$('.nav-item').forEach(i => i.classList.remove('active'));
  document.querySelector('[data-view="vocab"]').classList.add('active');
  loadVocab();
});

// 设置面板
const settingsModal = $('#settingsModal');
$('#settingsBtn').addEventListener('click', openSettings);
$('#settingsCancel').addEventListener('click', closeSettings);
settingsModal.addEventListener('click', (e) => { if (e.target === settingsModal) closeSettings(); });

// 服务商预设
const PROVIDER_PRESETS = {
  deepseek: { baseUrl: 'https://api.deepseek.com', model: 'deepseek-flash' },
  opencode: { baseUrl: 'https://opencode.ai/zen/v1', model: 'big-pickle' },
  openai: { baseUrl: 'https://api.openai.com/v1', model: 'gpt-4o-mini' },
  custom: { baseUrl: '', model: '' },
};

$('#settingProvider').addEventListener('change', (e) => {
  const preset = PROVIDER_PRESETS[e.target.value];
  if (preset) {
    $('#settingBaseUrl').value = preset.baseUrl;
    if (preset.model) {
      $('#settingModel').value = preset.model;
      $('#settingModelCustom').style.display = 'none';
    }
  }
});

$('#settingModel').addEventListener('change', (e) => {
  $('#settingModelCustom').style.display = e.target.value === 'custom' ? 'block' : 'none';
});

async function openSettings() {
  try {
    const s = await api.get('/api/settings');
    $('#settingBaseUrl').value = s.ai_base_url || '';
    $('#settingApiKey').value = s.ai_api_key || '';
    const model = s.ai_model || '';
    const modelSelect = $('#settingModel');
    const customInput = $('#settingModelCustom');
    if ([...modelSelect.options].some(o => o.value === model)) {
      modelSelect.value = model;
      customInput.style.display = 'none';
    } else {
      modelSelect.value = 'custom';
      customInput.value = model;
      customInput.style.display = 'block';
    }
  } catch(e) {}
  $('#testResult').className = 'test-result';
  $('#testResult').textContent = '';
  settingsModal.classList.add('show');
}
function closeSettings() { settingsModal.classList.remove('show'); }

// API 测试按钮
$('#testApiBtn').addEventListener('click', async () => {
  const btn = $('#testApiBtn');
  const result = $('#testResult');
  btn.disabled = true;
  btn.textContent = '测试中...';
  result.className = 'test-result';
  result.textContent = '';
  try {
    const resp = await fetch('/api/settings/test', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        ai_base_url: $('#settingBaseUrl').value,
        ai_api_key: $('#settingApiKey').value,
        ai_model: getCurrentModel()
      })
    });
    const data = await resp.json();
    if (data.ok) {
      result.className = 'test-result success';
      result.textContent = `连接成功 · 模型: ${data.model || '未知'}`;
    } else {
      result.className = 'test-result error';
      result.textContent = `连接失败: ${data.error || '未知错误'}`;
    }
  } catch (err) {
    result.className = 'test-result error';
    result.textContent = `连接失败: ${err.message}`;
  }
  btn.disabled = false;
  btn.textContent = '测试连接';
});

function getCurrentModel() {
  const sel = $('#settingModel').value;
  return sel === 'custom' ? $('#settingModelCustom').value.trim() : sel;
}

$('#settingsSave').addEventListener('click', async () => {
  await api.post('/api/settings', {
    ai_base_url: $('#settingBaseUrl').value,
    ai_api_key: $('#settingApiKey').value,
    ai_model: getCurrentModel(),
  });
  toast('设置已保存');
  closeSettings();
});

// 导航
// 页面切换：淡出 → 替换内容 → 淡入
let isSwitching = false;
async function switchPage(renderFn) {
  if (isSwitching) return;
  isSwitching = true;
  // 切换页面时停止打字机
  stopTypewriter();
  const reader = $('#reader');
  // 1. 淡出（只用opacity，减少重绘）
  reader.classList.add('page-fading');
  setTimeout(async () => {
    // 1.5 先切宽窄布局，再渲染内容。
    //     顺序有讲究：知识图谱 / 书架 / 材料这类宽内容要按容器真实宽度给
    //     canvas 定尺寸。如果在 renderFn 之后才加 wide-view，canvas 会先按
    //     阅读栏的窄宽度画好，再被容器拉宽 —— 整张图的比例就永久是错的。
    const activeView = document.querySelector('.nav-item.active')?.dataset.view;
    const WIDE_VIEWS = ['stats', 'articles', 'vocab', 'test', 'library', 'materials', 'graph'];
    reader.classList.toggle('wide-view', WIDE_VIEWS.includes(activeView));
    // 离开图谱页就释放图谱状态。收敛后物理循环已停，loop 里的 isConnected
    // 检查不会再执行，仅靠它清理会一直挂着节点和 canvas 引用。
    if (activeView !== 'graph') stopGraph();

    // 2. 替换内容 —— 必须 await！
    //    renderFn 里多是异步函数（loadStats / loadVocab / loadArticlesList…），
    //    它们要先拿到接口数据才写 innerHTML。不等它完成就淡入的话，
    //    会先把「上一页的旧内容」淡进来，等数据回来再被换掉 —— 这就是残影。
    try {
      await renderFn();
    } catch (err) {
      console.error('页面渲染失败:', err);
    }
    // 专注模式服务于「沉浸阅读」，只在阅读页显示入口；
    // 生词本 / 历史文章 / 测试 / 统计等页面隐藏，避免误触无意义的全屏。
    const focusBtn = $('#focusBtn');
    if (focusBtn) focusBtn.style.display = (activeView === 'reader') ? '' : 'none';
    // 底部计时栏也只属于阅读页 —— 出现在统计 / 生词本里很突兀。
    const timerBar = document.querySelector('.timer-bar');
    if (timerBar) timerBar.style.display = (activeView === 'reader') ? '' : 'none';
    // 3. 淡入
    reader.classList.remove('page-fading');
    reader.classList.remove('page-enter');
    void reader.offsetWidth; // 强制重排
    reader.classList.add('page-enter');
    $('#readerContainer').scrollTop = 0;
    setTimeout(() => {
      reader.classList.remove('page-enter');
      isSwitching = false;
    }, 520);
  }, 100);
}

$$('.nav-item').forEach(item => {
  item.addEventListener('click', () => {
    if (isSwitching) return;
    // 专注模式下切换到非阅读页，自动退出专注模式
    const view = item.dataset.view;
    if (focusMode && view !== 'reader') {
      toggleFocusMode();
    }
    $$('.nav-item').forEach(i => i.classList.remove('active'));
    item.classList.add('active');
    switchPage(() => {
      if (view === 'reader') {
        if (isGenerating) {
          // 正在生成中，显示等待动画
          startGeneratingAnimation();
        } else {
          // 每次进入阅读页都是待生成空状态
          currentArticle = null;
          autoSidebar = false;
          const app = document.querySelector('.app');
          if (app) app.classList.remove('auto-sidebar-mode');
          const sidebar = $('#sidebar');
          sidebar.classList.remove('collapsed');
          sidebar.classList.remove('sidebar-visible');
          $('#reader').innerHTML = `<div class="empty">
        <h2>ReadLoops</h2>
        <p>点击「生成文章」开始阅读训练</p>
        <div class="empty-hints">
          <span><kbd>空格</kbd>开始专注计时 · 暂停继续</span>
          <span><kbd>选中</kbd>查词 · 选整句翻译</span>
          <span><kbd>F</kbd>切换专注模式</span>
        </div>
      </div>`;
        }
      } else if (view === 'articles') loadArticlesList();
      else if (view === 'vocab') loadVocab();
      else if (view === 'test') loadTest();
      else if (view === 'stats') loadStats();
      else if (view === 'library') loadLibrary();
      else if (view === 'materials') loadMaterials();
      else if (view === 'graph') loadGraph();
    });
  });
});

async function loadArticlesList() {
  const articles = await api.get('/api/articles/?limit=30');
  $('#reader').innerHTML = '<h1>历史文章</h1><div class="article-list">' +
    (articles.length === 0 ? '<p style="color:var(--text-secondary)">还没有文章</p>' :
    articles.map(a =>
      `<div class="article-item" data-id="${a.id}">
        <div class="article-item-content">
          <div class="title">${escapeHtml(a.title)}</div>
          <div class="meta">${a.word_count} 词 · ${a.new_word_count} 生词 · ${new Date(a.created_at * 1000).toLocaleString('zh-CN', {month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'})}</div>
        </div>
        <button class="article-delete-btn" data-id="${a.id}" title="删除文章">×</button>
        <div class="article-detail-popup" data-id="${a.id}"></div>
      </div>`
    ).join('')) + '</div>';

  // 错峰浮现，避免几十条一起涌上来
  staggerIn('.article-item');

  // 点击文章打开
  $$('.article-item-content').forEach(item => {
    item.addEventListener('click', async () => {
      const articleItem = item.closest('.article-item');
      const article = await api.get(`/api/articles/${articleItem.dataset.id}`);
      currentArticle = article;
      $$('.nav-item').forEach(i => i.classList.remove('active'));
      document.querySelector('[data-view="reader"]').classList.add('active');
      switchPage(() => {
        renderArticle(article);
        $('#articleMeta').textContent = `${article.word_count} 词 · ${article.new_word_count} 生词`;
      });
    });
  });

  // 删除按钮
  $$('.article-delete-btn').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const id = btn.getAttribute('data-id');
      console.log('删除文章 id:', id, typeof id);
      if (!id || id === 'undefined') { await showAlert('文章ID无效'); return; }
      if (!await confirmDialog('确定删除这篇文章？\n它产生的遇见记录会一起删除，受影响的词重遇进度会相应回退。', { danger: true })) return;
      try {
        const url = window.location.origin + '/api/articles/' + id;
        console.log('删除URL:', url);
        const r = await fetch(url, { method: 'DELETE' });
        const result = await r.json();
        console.log('删除结果:', result);
        btn.closest('.article-item').remove();
        // 如果删除的是当前正在阅读的文章，清除 currentArticle
        if (currentArticle && String(currentArticle.id) === String(id)) {
          currentArticle = null;
        }
        toast('文章已删除');
      } catch(err) {
        console.error('删除错误:', err);
        await showAlert('删除失败: ' + err.message);
      }
    });
  });

  // 悬停显示详情
  let detailTimeout;
  $$('.article-item').forEach(item => {
    item.addEventListener('mouseenter', async () => {
      const id = item.dataset.id;
      const popup = item.querySelector('.article-detail-popup');
      clearTimeout(detailTimeout);
      detailTimeout = setTimeout(async () => {
        if (popup.dataset.loaded) return;
        try {
          const details = await api.get(`/api/articles/${id}/details`);
          popup.innerHTML = renderArticleDetail(details);
          popup.dataset.loaded = 'true';
        } catch(err) {
          popup.innerHTML = '<div style="padding:12px;color:var(--text-tertiary);">加载详情失败</div>';
        }
      }, 300);
    });
    item.addEventListener('mouseleave', () => {
      clearTimeout(detailTimeout);
    });
  });
}

function renderArticleDetail(details) {
  const topWords = details.top_words || [];
  const examSim = details.exam_similarity || [];
  const targetWords = details.target_words || [];

  return `
    <div class="article-detail-content">
      <div class="detail-section">
        <div class="detail-label">高频词</div>
        <div class="detail-tags">
          ${topWords.slice(0, 8).map(([w, c]) => `<span class="detail-tag">${w} <span class="tag-count">${c}</span></span>`).join('')}
        </div>
      </div>
      <div class="detail-section">
        <div class="detail-label">目标生词 (${targetWords.length})</div>
        <div class="detail-tags">
          ${targetWords.slice(0, 10).map(w => `<span class="detail-tag target">${w}</span>`).join('')}
          ${targetWords.length > 10 ? `<span class="detail-tag more">+${targetWords.length - 10}</span>` : ''}
        </div>
      </div>
      ${examSim.length > 0 ? `
      <div class="detail-section">
        <div class="detail-label">相似真题</div>
        <div class="exam-sim-list">
          ${examSim.map(s => `
            <div class="exam-sim-item">
              <span class="exam-sim-title">${escapeHtml(s.title.substring(0, 40))}</span>
              <span class="exam-sim-score" style="color:${s.similarity > 50 ? '#ff6b6b' : s.similarity > 30 ? '#f0a500' : '#4ade80'}">${s.similarity}%</span>
            </div>
          `).join('')}
        </div>
      </div>` : ''}
    </div>
  `;
}

let selectedWordIds = new Set();

async function loadVocab() {
  selectedWordIds.clear();
  const words = await api.get('/api/words/vocab?status=learning&limit=100');
  $('#reader').innerHTML = `
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:20px;">
      <button class="toolbar-btn" id="backToReaderBtn" style="display:flex;align-items:center;gap:6px;">← 返回阅读</button>
    </div>
    <h1>生词本 <span style="font-size:13px;color:var(--text-secondary);font-weight:400;">${words.length} 个词</span></h1>
    <div class="batch-add">
      <textarea id="batchWords" placeholder="粘贴任意词汇材料：每行一个、带释义、带音标、编号列表都可以&#10;abandon - 放弃，遗弃&#10;benefit n. 好处；v. 受益&#10;consequence /ˈkɒnsɪkwəns/ 后果"></textarea>
      <div class="batch-actions">
        <button class="toolbar-btn primary" id="batchAddBtn">AI 识别并预览</button>
        <button class="toolbar-btn" id="batchQuickAddBtn">直接添加</button>
        <span class="hint">AI 会识别词条、释义与等级；确认前可编辑</span>
      </div>
      <div id="batchPreview" class="batch-preview" style="display:none;"></div>
    </div>
    <div class="vocab-toolbar" id="vocabToolbar">
      <label class="select-all-label">
        <input type="checkbox" id="selectAllVocab"> 全选
      </label>
      <span id="selectedCount" style="font-size:12px;color:var(--text-secondary);">勾选单词后可批量操作</span>
      <div class="vocab-toolbar-spacer"></div>
      <button class="toolbar-btn" id="batchKnownBtn" disabled>标记已掌握</button>
      <button class="toolbar-btn danger" id="batchDeleteBtn" disabled>删除选中</button>
    </div>
    <div id="vocabList">` +
    (words.length === 0 ? '<p style="color:var(--text-secondary); padding:16px 0;">还没有生词</p>' :
    words.map(w =>
      `<div class="vocab-item" data-id="${w.id}">
        <div class="vocab-item-header">
          <input type="checkbox" class="vocab-checkbox" data-id="${w.id}" ${selectedWordIds.has(w.id) ? 'checked' : ''}>
          <div class="vocab-item-main" style="flex:1;cursor:pointer;">
            <div class="word-row">
              <span class="word">${escapeHtml(w.text)}</span>
              <span class="phonetic">${escapeHtml(w.phonetic || '')}</span>
            </div>
            <div class="meaning">${escapeHtml((w.meaning || '暂无释义').substring(0, 80))}${(w.meaning || '').length > 80 ? '...' : ''}</div>
          </div>
          <button class="vocab-delete-btn" data-id="${w.id}" title="删除">×</button>
        </div>
        <div class="vocab-detail" id="vocabDetail-${w.id}" style="display:none;">
          <div class="vocab-detail-section">
            <div class="vocab-detail-label">释义</div>
            <div class="vocab-detail-meaning">${escapeHtml(w.meaning || '暂无释义')}</div>
          </div>
          <div class="vocab-detail-meta">
            <span>遇见 ${w.encounter_count} 次</span>
            <span>查词 ${w.lookup_count} 次</span>
            <span>${w.level}</span>
            <span>添加于 ${new Date(w.created_at * 1000).toLocaleDateString('zh-CN')}</span>
          </div>
        </div>
        <!-- 悬停详情浮层（和文章卡片一致） -->
        <div class="vocab-detail-popup" data-id="${w.id}">
          <div class="detail-section">
            <div class="detail-label">${escapeHtml(w.text)} ${escapeHtml(w.phonetic || '')}</div>
            <div style="font-size:12px;line-height:1.6;color:var(--text);white-space:pre-wrap;max-height:120px;overflow-y:auto;">${escapeHtml(w.meaning || '暂无释义')}</div>
          </div>
          ${w.exchange ? `<div class="detail-section">
            <div class="detail-label">词形变化</div>
            <div class="detail-tags">${w.exchange.split(/[,;]/).filter(Boolean).slice(0,6).map(e => `<span class="detail-tag">${escapeHtml(e.trim())}</span>`).join('')}</div>
          </div>` : ''}
          <div class="detail-section">
            <div class="detail-label">学习统计</div>
            <div style="display:flex;flex-wrap:wrap;gap:8px;font-size:11px;color:var(--text-secondary);">
              <span>遇见 ${w.encounter_count} 次</span>
              <span>查词 ${w.lookup_count} 次</span>
              <span>正确 ${w.correct_count || 0} 次</span>
              <span>错误 ${w.wrong_count || 0} 次</span>
            </div>
          </div>
          <div class="detail-section" style="margin-bottom:0;">
            <div class="detail-label">记忆状态</div>
            <div style="font-size:11px;color:var(--text-tertiary);">
              ${w.srs_stability ? `强度 ${Math.round(w.srs_stability)} · 间隔 ${Math.round(w.srs_interval || 0)}天` : '未开始复习'}
              ${w.last_test_at ? ` · 上次测试 ${new Date(w.last_test_at * 1000).toLocaleDateString('zh-CN')}` : ''}
            </div>
          </div>
        </div>
      </div>`
    ).join('')) + '</div>';

  // 错峰浮现
  staggerIn('.vocab-item');

  // 返回阅读
  const backBtn = $('#backToReaderBtn');
  if (backBtn) {
    backBtn.addEventListener('click', () => {
      $$('.nav-item').forEach(i => i.classList.remove('active'));
      document.querySelector('[data-view="reader"]').classList.add('active');
      if (currentArticle) {
        renderArticle(currentArticle);
      } else {
        $('#reader').innerHTML = `<div class="empty">
        <h2>ReadLoops</h2>
        <p>点击「生成文章」开始阅读训练</p>
        <div class="empty-hints">
          <span><kbd>空格</kbd>开始专注计时 · 暂停继续</span>
          <span><kbd>选中</kbd>查词 · 选整句翻译</span>
          <span><kbd>F</kbd>切换专注模式</span>
        </div>
      </div>`;
      }
    });
  }

  // 批量添加：默认走「识别 → 可编辑预览 → 确认」，保留直接添加作为无等待快捷方式。
  $('#batchAddBtn').addEventListener('click', async () => {
    const text = $('#batchWords').value.trim();
    if (!text) { toast('请输入生词'); return; }
    const btn = $('#batchAddBtn');
    btn.disabled = true;
    btn.textContent = '识别中…';
    try {
      const result = await api.post('/api/words/batch-recognize', { text });
      renderBatchPreview(result);
    } catch (e) {
      toast(e.message || '识别失败');
    } finally {
      btn.disabled = false;
      btn.textContent = 'AI 识别并预览';
    }
  });

  $('#batchQuickAddBtn').addEventListener('click', async () => {
    const text = $('#batchWords').value.trim();
    if (!text) { toast('请输入生词'); return; }
    // 直接模式只取纯英文 token，避免把中文释义和词性缩写带入库。
    const ws = [...new Set((text.match(/\b[a-zA-Z][a-zA-Z'-]*\b/g) || [])
      .map(w => w.toLowerCase())
      .filter(w => !['n','v','vi','vt','adj','adv','prep','conj','pron','num','art'].includes(w)))];
    if (!ws.length) { toast('没有有效的英文词条'); return; }
    const result = await api.post('/api/words/batch-add', { words: ws });
    toast(batchAddSummary(result));
    $('#batchWords').value = '';
    loadVocab();
  });

  function renderBatchPreview(result) {
    const box = $('#batchPreview');
    const items = result.items || [];
    if (!items.length) {
      box.style.display = 'block';
      box.innerHTML = `<div class="batch-preview-empty">没有识别到有效英文词条。可尝试每行一个词，或用「单词 - 释义」格式。</div>`;
      return;
    }
    box.style.display = 'block';
    box.innerHTML = `
      <div class="batch-preview-head">
        <span>识别到 <b>${items.length}</b> 个词条</span>
        <span class="batch-preview-mode">${result.mode === 'ai' ? 'AI 识别' : '本地解析'}</span>
      </div>
      ${result.warning ? `<div class="batch-preview-warning">${escapeHtml(result.warning)}</div>` : ''}
      <div class="batch-preview-list">
        ${items.map((it, idx) => `
          <div class="batch-preview-item" data-index="${idx}">
            <input type="checkbox" class="batch-item-check" checked title="是否加入">
            <input class="batch-item-word" value="${escapeHtml(it.word)}" aria-label="词条">
            <input class="batch-item-meaning" value="${escapeHtml(it.meaning || '')}" placeholder="释义（可编辑）" aria-label="释义">
            <select class="batch-item-level" aria-label="等级">
              ${['CET4','CET6','other'].map(l => `<option value="${l}" ${it.level === l ? 'selected' : ''}>${l}</option>`).join('')}
            </select>
            <button class="batch-item-remove" title="移除">×</button>
          </div>`).join('')}
      </div>
      <div class="batch-preview-actions">
        <label class="batch-preview-select"><input type="checkbox" id="batchPreviewAll" checked> 全选</label>
        <span class="hint" id="batchPreviewCount">已选 ${items.length} 个</span>
        <div class="batch-preview-spacer"></div>
        <button class="toolbar-btn" id="batchPreviewCancel">取消</button>
        <button class="toolbar-btn primary" id="batchPreviewConfirm">确认加入</button>
      </div>`;

    const updateCount = () => {
      const checks = $$('.batch-item-check');
      const checked = checks.filter(c => c.checked).length;
      $('#batchPreviewCount').textContent = `已选 ${checked} 个`;
      $('#batchPreviewAll').checked = checked === checks.length && checks.length > 0;
    };
    $$('.batch-item-check').forEach(c => c.addEventListener('change', updateCount));
    $('#batchPreviewAll').addEventListener('change', e => {
      $$('.batch-item-check').forEach(c => { c.checked = e.target.checked; });
      updateCount();
    });
    $$('.batch-item-remove').forEach(btn => btn.addEventListener('click', () => {
      btn.closest('.batch-preview-item').remove();
      updateCount();
    }));
    $('#batchPreviewCancel').addEventListener('click', () => { box.style.display = 'none'; box.innerHTML = ''; });
    $('#batchPreviewConfirm').addEventListener('click', async () => {
      const selected = $$('.batch-preview-item').filter(row => row.querySelector('.batch-item-check').checked);
      const normalized = selected.map(row => ({
        word: row.querySelector('.batch-item-word').value.trim(),
        meaning: row.querySelector('.batch-item-meaning').value.trim(),
        level: row.querySelector('.batch-item-level').value,
      })).filter(i => i.word);
      if (!normalized.length) { toast('请至少保留一个词条'); return; }
      const btn = $('#batchPreviewConfirm');
      btn.disabled = true;
      btn.textContent = '加入中…';
      try {
        const r = await api.post('/api/words/batch-add', { items: normalized });
        toast(batchAddSummary(r));
        $('#batchWords').value = '';
        loadVocab();
      } catch (e) {
        toast(e.message || '批量添加失败');
        btn.disabled = false;
        btn.textContent = '确认加入';
      }
    });
  }

  function batchAddSummary(r) {
    const parts = [];
    if (r.added) parts.push(`新增 ${r.added}`);
    if (r.activated) parts.push(`加入学习 ${r.activated}`);
    if (r.skipped) parts.push(`跳过 ${r.skipped}`);
    return parts.length ? parts.join(' · ') : '没有可加入的词条';
  }

  // 词条点击展开/折叠
  $$('.vocab-item-main').forEach(el => {
    el.addEventListener('click', () => {
      const item = el.closest('.vocab-item');
      const id = item.dataset.id;
      const detail = document.getElementById(`vocabDetail-${id}`);
      if (detail.style.display === 'none') {
        detail.style.display = 'block';
      } else {
        detail.style.display = 'none';
      }
    });
  });

  // 单个删除
  $$('.vocab-delete-btn').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const id = btn.dataset.id;
      if (!await confirmDialog('把这个词移出生词本？\n（词典条目会保留，学习记录清零）', { danger: true })) return;
      const r = await api.delete(`/api/words/${id}`);
      toast(r && r.message ? r.message : '已移出生词本');
      loadVocab();
    });
  });

  // 多选
  $$('.vocab-checkbox').forEach(cb => {
    cb.addEventListener('click', (e) => e.stopPropagation());
    cb.addEventListener('change', () => {
      const id = parseInt(cb.dataset.id);
      if (cb.checked) selectedWordIds.add(id);
      else selectedWordIds.delete(id);
      updateVocabToolbar();
    });
  });

  // 全选
  const selectAll = $('#selectAllVocab');
  if (selectAll) {
    selectAll.addEventListener('change', () => {
      $$('.vocab-checkbox').forEach(cb => {
        cb.checked = selectAll.checked;
        const id = parseInt(cb.dataset.id);
        if (selectAll.checked) selectedWordIds.add(id);
        else selectedWordIds.delete(id);
      });
      updateVocabToolbar();
    });
  }

  // 批量删除
  const batchDel = $('#batchDeleteBtn');
  if (batchDel) {
    batchDel.addEventListener('click', async () => {
      if (selectedWordIds.size === 0) { toast('请先选择'); return; }
      if (!await confirmDialog(`把选中的 ${selectedWordIds.size} 个词移出生词本？\n（词典条目会保留，学习记录清零）`, { danger: true })) return;
      const r = await api.post('/api/words/batch-delete', { word_ids: [...selectedWordIds] });
      toast(r && r.message ? r.message : `已移出 ${selectedWordIds.size} 个词`);
      loadVocab();
    });
  }

  // 批量标记已掌握
  const batchKnown = $('#batchKnownBtn');
  if (batchKnown) {
    batchKnown.addEventListener('click', async () => {
      if (selectedWordIds.size === 0) { toast('请先选择'); return; }
      await api.post('/api/words/batch-status', { word_ids: [...selectedWordIds], status: 'known' });
      toast(`已标记 ${selectedWordIds.size} 个词为已掌握`);
      loadVocab();
    });
  }

}

function updateVocabToolbar() {
  const toolbar = $('#vocabToolbar');
  if (!toolbar) return;
  const count = selectedWordIds.size;
  const hasSelection = count > 0;
  // 工具栏常驻显示：之前默认隐藏，导致用户根本找不到批量操作入口。
  // 改为常驻 + 未选中时按钮置灰，功能一眼可见。
  toolbar.style.display = 'flex';
  const counter = $('#selectedCount');
  if (counter) counter.textContent = hasSelection ? `已选 ${count} 个` : '勾选单词后可批量操作';
  const knownBtn = $('#batchKnownBtn');
  const delBtn = $('#batchDeleteBtn');
  if (knownBtn) knownBtn.disabled = !hasSelection;
  if (delBtn) delBtn.disabled = !hasSelection;
  const selectAll = $('#selectAllVocab');
  if (selectAll) {
    const total = $$('.vocab-checkbox').length;
    selectAll.checked = hasSelection && count === total;
  }
}

/** 词汇差距区块：覆盖率仪表 + 各考试缺口 + 进度预测。
 *
 * 覆盖率不是「词表掌握百分比」—— 实测 CET4 大纲词表只覆盖四级真题语料
 * 15.8% 的 token，剩下 84% 是基础词与功能词，拿词表百分比当「能读懂多少」会严重失真。
 * 这里显示的是按词频档位加权的**真实覆盖率**，也就是 i+1 判据本身。
 */
function renderVocabGap(g, examSel, mastery) {
  const cov = g.coverage && g.coverage.CET4 && g.coverage.CET4.available ? g.coverage.CET4 : null;
  const c6 = g.coverage && g.coverage.CET6 && g.coverage.CET6.available ? g.coverage.CET6 : null;
  const pct = (v) => (v * 100).toFixed(1);

  const gauge = cov ? (() => {
    const c = cov.coverage * 100;
    const low = cov.i1_low * 100, high = cov.i1_high * 100;
    const verdict = cov.in_i1
      ? `在 i+1 区间内 —— 每 300 词约 ${cov.unknown_per_300.toFixed(0)} 个生词，正是高效吸收的密度`
      : (c < low
        ? `尚未进入可理解输入区间（${low}%），每 300 词约 <b>${cov.unknown_per_300.toFixed(0)}</b> 个生词，读起来会偏吃力`
        : `已超过 i+1 上沿（${high}%），材料偏简单，可以上难度了`);
    return `
    <div class="gap-cover">
      <div class="gap-cover-head">
        <span>四级真题覆盖率</span>
        <span class="gap-cover-val">${pct(cov.coverage)}%</span>
      </div>
      <div class="gap-gauge">
        <div class="gap-gauge-i1" style="left:${low}%;width:${high - low}%"></div>
        <div class="gap-gauge-fill" style="width:${c}%"></div>
        <div class="gap-gauge-mark" style="left:${c}%"></div>
      </div>
      <div class="gap-gauge-scale">
        <span>0%</span>
        <span class="gap-i1-label">i+1 可理解输入区间 ${low}–${high}%</span>
        <span>100%</span>
      </div>
      <div class="gap-verdict ${cov.in_i1 ? 'ok' : ''}">${verdict}</div>
      ${c6 ? `<div class="gap-minor">六级真题覆盖率 ${pct(c6.coverage)}%（每 300 词约 ${c6.unknown_per_300.toFixed(0)} 个生词）</div>` : ''}
      <div class="gap-source">基于 ${cov.passages} 篇四级真题 / ${cov.tokens.toLocaleString()} 词实测</div>
    </div>`;
  })() : '<div class="gap-verdict">真题语料不可用（运行 tools/crawl_exam_corpus.py 抓取）</div>';

  const exams = (g.exams || []).map(e => {
    const filled = Math.round(e.progress * 22);
    return `
      <div class="meter-row" title="已掌握 ${e.known} / ${e.size}">
        <span class="meter-label gap-exam-name">${e.name}</span>
        <span class="meter-track"><span class="meter-fill" style="width:${Math.round(e.progress * 100)}%"></span></span>
        <span class="meter-val">${e.known}/${e.size}</span>
        <span class="gap-exam-rest">还差 ${e.gap}</span>
      </div>`;
  }).join('');

  const rate = g.rate || {};
  const etaLine = (() => {
    if (!rate.enough_data) {
      return '学够一段时间（累计新掌握 10 词以上）后才好估算进度 —— 现在给数字只会是编的。';
    }
    return `近 ${rate.days} 天日均新掌握 <b>${rate.per_day}</b> 词。`;
  })();

  return `
    <div class="stats-section gap-section">
      <h2 class="stats-section-title">词汇差距</h2>

      <div class="gap-head">
        <div>
          <div class="gap-vocab">${g.vocab_estimate.toLocaleString()}${g.is_lower_bound ? '+' : ''} <small>词</small></div>
          <div class="gap-sub">${g.using_default
            ? '默认假设：掌握最高频 2,500 词。<b>这不是测出来的</b> —— 做一次定级测试才有真实数字。'
            : `来自你的词汇量定级测试（伪词虚报率 ${(g.false_alarm * 100).toFixed(1)}%）`}</div>
        </div>
        <button class="toolbar-btn ${g.using_default ? 'primary' : ''}"
                onclick="switchPage(loadPlacement)">${g.using_default ? '去做定级测试' : '重新测试'}</button>
      </div>

      ${gauge}

      <div class="gap-exams">
        <div class="gap-exam-head">
          <span class="meter-title" style="margin:0">各考试缺口</span>
          ${examSel ? `
          <label class="gap-exam-pick">
            <span>目标考试</span>
            <select id="targetExamSelect">
              ${examSel.options.map(o => `<option value="${o.key}" ${o.key === examSel.exam ? 'selected' : ''}>${o.label}</option>`).join('')}
            </select>
          </label>` : ''}
        </div>
        <div class="gap-exam-note">目标考试决定下一篇埋哪些词 —— 缺口的缩小速度由它导向。</div>
        <div class="gap-exam-list">${exams}</div>
        <div class="gap-eta">${etaLine}</div>
      </div>

      ${mastery ? `
      <div class="gap-mastery">
        <div class="meter-title">掌握度分布（识别与回忆分开计）</div>
        <div class="gap-mastery-note">
          依据 <b>Pellicer-Sánchez (2015)</b>：8 次语境遇见能建立 <b>86% 词形识别 / 75% 意义识别</b>，
          但回忆只有 <b>55%</b>。所以「认得出来」和「想得起来」分开记 ——
          前者该继续输入，后者才该去做提取练习。
        </div>
        <div class="gap-mastery-grid">
          <div><b>${mastery.by_level.seen}</b><span>词 · 见过面</span></div>
          <div><b>${mastery.by_level.recognized}</b><span>词 · 认得出来</span></div>
          <div><b>${mastery.by_level.recalled}</b><span>词 · 想得起来</span></div>
          <div><b>${(mastery.phrases && mastery.phrases.seen) || 0}</b><span>短语 · 已接触</span></div>
        </div>
        <div class="gap-mastery-note" style="margin-top:10px">
          <b>短语和单词同等重要。</b>方法里「常用 3000 词其实不止 3000 个词条」——
          多词组合语（固定搭配）各算一条，所以短语走的是**和单词完全相同**的
          选词、重遇与掌握度机制，只是从各自的池子里挑，互不挤占名额。
        </div>
      </div>` : ''}
    </div>
  `;
}

async function loadStats() {
  const [s, activity, reentry, placement, gap, examSel, mastery] = await Promise.all([
    api.get('/api/stats/overview'),
    api.get('/api/stats/activity?weeks=26'),
    api.get('/api/stats/reentry?limit=12').catch(() => ({ summary: null, items: [] })),
    api.get('/api/placement/summary').catch(() => ({ has_result: false })),
    api.get('/api/stats/vocab-gap').catch(() => null),
    api.get('/api/stats/target-exam').catch(() => null),
    api.get('/api/stats/mastery').catch(() => null),
  ]);
  window._targetExam = examSel; window._mastery = mastery;
  const mins = Math.floor(s.total_reading_seconds / 60);
  const hours = Math.floor(mins / 60);
  const timeStr = hours > 0 ? `${hours}小时${mins % 60}分` : `${mins}分钟`;

  // 渲染热力图
  const heatmapHTML = activity.days.map(d => {
    const mins = Math.floor(d.reading_seconds / 60);
    const tooltip = `${d.date}\n${d.articles}篇文章 · ${mins}分钟 · ${d.lookups}次查词`;
    return `<div class="heatmap-cell level-${d.level}" data-tooltip="${tooltip}" data-date="${d.date}"></div>`;
  }).join('');

  $('#reader').innerHTML = `
    <h1>学习统计</h1>

    <!-- 词汇差距（本轮新增：把「提升词汇量」变成可计算的闭环）-->
    ${gap ? renderVocabGap(gap, examSel, mastery) : ''}

    <!-- 概览卡片 -->
    <div class="stats-grid" style="grid-template-columns: repeat(4, 1fr);">
      <div class="stat-card">
        <div class="num">${s.articles_read}</div>
        <div class="label">已读文章</div>
      </div>
      <div class="stat-card">
        <div class="num">${s.total_words.toLocaleString()}</div>
        <div class="label">总阅读词数</div>
      </div>
      <div class="stat-card">
        <div class="num">${timeStr}</div>
        <div class="label">阅读时长</div>
      </div>
      <div class="stat-card">
        <div class="num">${s.streak_days}</div>
        <div class="label">连续学习天数</div>
      </div>
      <div class="stat-card" style="cursor:pointer" onclick="switchPage(loadPlacement)">
        <div class="num">${(placement.has_result ? placement.vocab_estimate : s.vocab_estimate).toLocaleString()}${placement.has_result && placement.is_lower_bound ? '+' : ''}</div>
        <div class="label">${gap && !gap.using_default ? '词汇量（已定级）' : '词汇量（默认假设）'}</div>
      </div>
      <div class="stat-card">
        <div class="num">${s.test_accuracy}%</div>
        <div class="label">测试正确率</div>
      </div>
      <div class="stat-card">
        <div class="num">${s.total_lookups}</div>
        <div class="label">查词次数</div>
      </div>
      <div class="stat-card">
        <div class="num">${s.srs.due_today}</div>
        <div class="label">今日待复习</div>
      </div>
    </div>

    <!-- 重遇进度 -->
    ${reentry.summary && reentry.summary.tracked > 0 ? `
    <div class="stats-section">
      <h2 class="stats-section-title">重遇进度</h2>
      <div class="reentry-note">
        方法要求：同一个生词要在<b>变化的语境</b>里重遇 ${reentry.summary.target} 次 ——
        所以这里数的是「跨了多少篇文章」，而不是「看了几眼」。
      </div>
      <div class="reentry-head">
        <span>${reentry.summary.done} / ${reentry.summary.tracked} 个学习词已达标</span>
        <span class="reentry-avg">平均跨 ${reentry.summary.avg_articles} 篇</span>
      </div>
      <div class="reentry-list">
      ${reentry.items.map(it => `
        <div class="meter-row" title="${escapeHtml(it.meaning)}">
          <span class="meter-label reentry-word">${escapeHtml(it.text)}</span>
          <span class="meter-track"><span class="meter-fill" style="width:${Math.round(it.progress * 100)}%"></span></span>
          <span class="meter-val">${it.articles}/${it.target}</span>
        </div>`).join('')}
      </div>
    </div>` : ''}

    <!-- 阅读热力图 -->
    <div class="stats-section">
      <h2 class="stats-section-title">阅读活动 · 近26周（活跃 ${activity.active_days} 天）</h2>
      <div class="heatmap-container">
        <div class="heatmap">${heatmapHTML}</div>
      </div>
      <div class="heatmap-legend">
        <span>少</span>
        <div class="heatmap-cell"></div>
        <div class="heatmap-cell level-1"></div>
        <div class="heatmap-cell level-2"></div>
        <div class="heatmap-cell level-3"></div>
        <div class="heatmap-cell level-4"></div>
        <span>多</span>
      </div>
    </div>

    <!-- 词汇状态 -->
    <div class="stats-section">
      <h2 class="stats-section-title">词汇状态</h2>
      <div class="vocab-status-bar">
        <div class="vocab-status-item" style="flex:${s.words_known}">
          <span class="vocab-status-label">已掌握 ${s.words_known}</span>
        </div>
        <div class="vocab-status-item learning" style="flex:${s.words_learning}">
          <span class="vocab-status-label">学习中 ${s.words_learning}</span>
        </div>
        <div class="vocab-status-item target" style="flex:${s.words_target}">
          <span class="vocab-status-label">测验词 ${s.words_target}</span>
        </div>
      </div>
    </div>

    <!-- FSRS 记忆统计 -->
    <div class="stats-section">
      <h2 class="stats-section-title">FSRS 记忆系统</h2>
      <div class="stats-grid" style="grid-template-columns: repeat(3, 1fr);">
        <div class="stat-card">
          <div class="num">${s.srs.total}</div>
          <div class="label">纳入复习词数</div>
        </div>
        <div class="stat-card">
          <div class="num">${s.srs.avg_stability}</div>
          <div class="label">平均记忆稳定性</div>
        </div>
        <div class="stat-card">
          <div class="num">${s.srs.due_today}</div>
          <div class="label">今日到期复习</div>
        </div>
      </div>
    </div>

    <!-- 查词热词 -->
    ${s.hot_words && s.hot_words.length > 0 ? `
    <div class="stats-section">
      <h2 class="stats-section-title">查词热词 TOP ${s.hot_words.length}</h2>
      <div class="hot-words-list">
        ${s.hot_words.map((w, i) => `
          <div class="hot-word-item">
            <span class="hot-word-rank">${i + 1}</span>
            <span class="hot-word-text">${escapeHtml(w.text)}</span>
            <div class="hot-word-bar-bg">
              <div class="hot-word-bar" style="width:${(w.count / s.hot_words[0].count * 100)}%"></div>
            </div>
            <span class="hot-word-count">${w.count}次</span>
          </div>
        `).join('')}
      </div>
    </div>` : ''}
  `;

  // 热力图 tooltip
  initHeatmapTooltip();
}

// 热力图悬停提示
function initHeatmapTooltip() {
  let tooltip = document.getElementById('heatmapTooltip');
  if (!tooltip) {
    tooltip = document.createElement('div');
    tooltip.id = 'heatmapTooltip';
    tooltip.className = 'heatmap-tooltip';
    document.body.appendChild(tooltip);
  }
  $$('.heatmap-cell').forEach(cell => {
    cell.addEventListener('mouseenter', (e) => {
      const data = cell.dataset.tooltip.split('\n');
      tooltip.innerHTML = `<div class="tt-date">${data[0]}</div><div class="tt-row">${data[1]}</div>`;
      tooltip.classList.add('show');
    });
    cell.addEventListener('mousemove', (e) => {
      tooltip.style.left = (e.clientX + 12) + 'px';
      tooltip.style.top = (e.clientY - 10) + 'px';
    });
    cell.addEventListener('mouseleave', () => {
      tooltip.classList.remove('show');
    });
  });
}

// 单词测试
let testQuestions = [];
let testCurrent = 0;
let testScore = 0;
let testAnswered = false;
let testConfig = { type: 'vocab', count: 20, difficulty: 'mixed' };
let testWrongAnswers = [];

async function loadTest() {
  testQuestions = [];
  testCurrent = 0;
  testScore = 0;
  testAnswered = false;
  testWrongAnswers = [];
  // 获取 FSRS 统计
  try {
    const srsStats = await api.get('/api/words/srs-stats');
    window._srsStats = srsStats;
  } catch(e) { window._srsStats = null; }
  // 定级结果是统计页与选词的起点，这里顺带取一次用于入口提示
  try {
    window._placement = await api.get('/api/placement/summary');
  } catch(e) { window._placement = { has_result: false }; }
  renderTestHome();
}

function renderTestHome() {
  const srs = window._srsStats;
  $('#reader').innerHTML = `
    <h1>单词测试</h1>
    <div class="test-home">
      ${srs && srs.due_today > 0 ? `
      <div class="srs-review-banner" onclick="startSrsReview()">
        <div class="srs-review-info">
          <span class="srs-review-count">${srs.due_today}</span>
          <span class="srs-review-label">个词今日待复习</span>
        </div>
        <button class="toolbar-btn primary">开始复习</button>
      </div>` : ''}
      <div class="placement-entry ${window._placement && window._placement.has_result ? '' : 'pending'}"
           onclick="switchPage(loadPlacement)">
        <div class="placement-entry-main">
          <div class="placement-entry-title">词汇量定级</div>
          <div class="placement-entry-desc">${window._placement && window._placement.has_result
            ? `当前估计 ${window._placement.vocab_estimate.toLocaleString()} 词 · 点此重新测试`
            : '还没测过 —— 先花 3–5 分钟定级，统计和选词才有依据'}</div>
        </div>
        <button class="toolbar-btn ${window._placement && window._placement.has_result ? '' : 'primary'}">
          ${window._placement && window._placement.has_result ? '查看' : '开始'}
        </button>
      </div>
      <div class="test-section">
        <div class="test-section-title">测试类型</div>
        <div class="test-type-grid">
          <div class="test-type-card ${testConfig.type === 'vocab' ? 'active' : ''}" data-type="vocab">
            <div class="test-type-name">词汇量测试</div>
            <div class="test-type-desc">从全部词库随机抽题</div>
          </div>
          <div class="test-type-card ${testConfig.type === 'learning' ? 'active' : ''}" data-type="learning">
            <div class="test-type-name">生词复习</div>
            <div class="test-type-desc">只考生词本中的词</div>
          </div>
          <div class="test-type-card ${testConfig.type === 'target' ? 'active' : ''}" data-type="target">
            <div class="test-type-name">测验词复习</div>
            <div class="test-type-desc">只考查过2次的目标词</div>
          </div>
          <div class="test-type-card disabled" data-type="grammar">
            <div class="test-type-name">语法测试</div>
            <div class="test-type-desc">即将上线</div>
          </div>
        </div>
      </div>

      <div class="test-section">
        <div class="test-section-title">题量</div>
        <div class="test-option-group">
          ${[10, 20, 50].map(n =>
            `<button class="test-option-btn ${testConfig.count === n ? 'active' : ''}" data-count="${n}">${n} 题</button>`
          ).join('')}
        </div>
      </div>

      <div class="test-section">
        <div class="test-section-title">难度</div>
        <div class="test-option-group">
          ${[
            {v: 'easy', l: '简单（高频词）'},
            {v: 'medium', l: '中等（中频词）'},
            {v: 'hard', l: '困难（低频词）'},
            {v: 'mixed', l: '混合'}
          ].map(d =>
            `<button class="test-option-btn ${testConfig.difficulty === d.v ? 'active' : ''}" data-difficulty="${d.v}">${d.l}</button>`
          ).join('')}
        </div>
      </div>

      <button class="toolbar-btn primary test-start-btn" id="startTestBtn">开始测试</button>
    </div>`;

  // 测试类型选择
  $$('.test-type-card').forEach(card => {
    if (card.classList.contains('disabled')) return;
    card.addEventListener('click', () => {
      testConfig.type = card.dataset.type;
      $$('.test-type-card').forEach(c => c.classList.remove('active'));
      card.classList.add('active');
    });
  });

  // 题量选择
  $$('[data-count]').forEach(btn => {
    btn.addEventListener('click', () => {
      testConfig.count = parseInt(btn.dataset.count);
      $$('[data-count]').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
    });
  });

  // 难度选择
  $$('[data-difficulty]').forEach(btn => {
    btn.addEventListener('click', () => {
      testConfig.difficulty = btn.dataset.difficulty;
      $$('[data-difficulty]').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
    });
  });

  $('#startTestBtn').addEventListener('click', startTest);

  // 分区与选项错峰浮现，和全站节奏保持一致
  staggerIn('.srs-review-banner');
  staggerIn('.test-type-card', 0.04);
  staggerIn('.test-option-btn', 0.03);
}

async function startSrsReview() {
  // 开始 FSRS 今日复习测试
  try {
    const dueWords = await api.get('/api/words/review-due?limit=20');
    if (dueWords.length === 0) {
      toast('今天没有待复习的词');
      return;
    }
    // 创建测试记录
    let testId = null;
    try {
      const testRes = await api.post('/api/words/test/start', {});
      testId = testRes.test_id;
    } catch(e) { console.log('创建测试记录失败', e); }

    // 生成题目（从到期词中抽）
    testQuestions = await api.get(
      `/api/words/test/generate?count=${Math.min(20, dueWords.length)}&difficulty=mixed&source=vocab`
    );
    if (testQuestions.length === 0) {
      toast('没有足够的题目');
      return;
    }
    window._currentTestId = testId;
    testCurrent = 0;
    testScore = 0;
    testAnswered = false;
    testWrongAnswers = [];
    renderTestQuestion();
    toast(`开始复习 ${dueWords.length} 个词`);
  } catch(err) {
    console.error(err);
    toast('开始复习失败');
  }
}

async function startTest() {
  const sourceMap = { vocab: 'adaptive', learning: 'vocab', target: 'target' };
  const source = sourceMap[testConfig.type] || 'adaptive';
  try {
    // 创建测试记录
    let testId = null;
    try {
      const testRes = await api.post('/api/words/test/start', {});
      testId = testRes.test_id;
    } catch(e) { console.log('创建测试记录失败', e); }

    testQuestions = await api.get(
      `/api/words/test/generate?count=${testConfig.count}&difficulty=${testConfig.difficulty}&source=${source}`
    );
    if (testQuestions.length === 0) {
      toast('没有足够的题目，请换个范围或难度');
      return;
    }
    testCurrent = 0;
    testScore = 0;
    testWrongAnswers = [];
    window.currentTestId = testId;
    renderTestQuestion();
  } catch (e) {
    $('#reader').innerHTML = '<h1>单词测试</h1><p style="color:var(--text-secondary);">生成题目失败，请重试</p>';
  }
}

function renderTestQuestion() {
  if (testCurrent >= testQuestions.length) {
    renderTestResult();
    return;
  }
  testAnswered = false;
  const q = testQuestions[testCurrent];
  const startTime = Date.now();

  // 题型显示
  let questionDisplay = '';
  if (q.type === 'word_to_meaning') {
    questionDisplay = `<div class="test-word">${escapeHtml(q.word)}</div>`;
  } else if (q.type === 'meaning_to_word') {
    questionDisplay = `<div class="test-question-text">${escapeHtml(q.question)}</div>`;
  } else if (q.type === 'word_form') {
    questionDisplay = `<div class="test-question-text">${escapeHtml(q.question)}</div>`;
  } else {
    questionDisplay = `<div class="test-word">${escapeHtml(q.word)}</div>`;
  }

  $('#reader').innerHTML = `
    <div class="test-header">
      <span>第 ${testCurrent + 1} / ${testQuestions.length} 题</span>
      <span>得分：${testScore}</span>
    </div>
    <div class="test-progress"><div class="test-progress-bar" style="width:${(testCurrent / testQuestions.length) * 100}%"></div></div>
    ${questionDisplay}
    <div class="test-options">
      ${q.options.map((opt, i) =>
        `<button class="test-option" data-index="${i}">${String.fromCharCode(65 + i)}. ${escapeHtml(opt)}</button>`
      ).join('')}
    </div>`;
  // 选项错峰浮现
  staggerIn('.test-option', 0.035);

  $$('.test-option').forEach(btn => {
    btn.addEventListener('click', () => {
      if (testAnswered) return;
      testAnswered = true;
      const selected = btn.dataset.index;
      const correct = q.options.indexOf(q.answer);
      const isCorrect = parseInt(selected) === correct;
      const reactionTime = Date.now() - startTime;

      if (isCorrect) {
        testScore++;
        btn.classList.add('correct');
      } else {
        btn.classList.add('wrong');
        $$('.test-option')[correct].classList.add('correct');
        testWrongAnswers.push({ word: q.word, answer: q.answer, selected: q.options[selected] });
      }

      // 提交结果到后端
      if (window.currentTestId && q.word_id) {
        api.post(`/api/words/test/${window.currentTestId}/submit?word_id=${q.word_id}&is_correct=${isCorrect}&reaction_time=${reactionTime}`, {})
          .catch(e => console.log('提交结果失败', e));
      }

      setTimeout(() => { testCurrent++; renderTestQuestion(); }, 1000);
    });
  });
}

function renderTestResult() {
  // 记录今天已测试
  localStorage.setItem('readloops_last_test_date', new Date().toDateString());

  const pct = Math.round((testScore / testQuestions.length) * 100);
  let level = '', levelColor = '';
  if (pct >= 90) { level = '优秀'; levelColor = '#4ade80'; }
  else if (pct >= 70) { level = '良好'; levelColor = '#60a5fa'; }
  else if (pct >= 50) { level = '及格'; levelColor = '#fbbf24'; }
  else { level = '需加强'; levelColor = '#ff6b6b'; }

  const typeNames = { vocab: '词汇量测试', learning: '生词复习', target: '测验词复习' };
  const diffNames = { easy: '简单', medium: '中等', hard: '困难', mixed: '混合' };

  $('#reader').innerHTML = `
    <h1>测试结果</h1>
    <div class="test-result-card">
      <div class="test-score" style="color:${levelColor}">${testScore} / ${testQuestions.length}</div>
      <div class="test-percent">${pct}%</div>
      <div class="test-level" style="color:${levelColor}">${level}</div>
      <div class="test-meta">
        <span>${typeNames[testConfig.type] || testConfig.type}</span>
        <span>${diffNames[testConfig.difficulty]}</span>
        <span>${testQuestions.length} 题</span>
      </div>
    </div>

    ${testWrongAnswers.length > 0 ? `
    <div class="test-wrong-section">
      <div class="test-section-title">错题回顾（${testWrongAnswers.length} 题）</div>
      <div class="test-wrong-list">
        ${testWrongAnswers.map(w => `
          <div class="test-wrong-item">
            <div class="test-wrong-word">${escapeHtml(w.word)}</div>
            <div class="test-wrong-correct">正确：${escapeHtml(w.answer)}</div>
            <div class="test-wrong-selected">你的选择：${escapeHtml(w.selected)}</div>
          </div>
        `).join('')}
      </div>
    </div>` : ''}

    <div style="display:flex;gap:10px;margin-top:24px;flex-wrap:wrap;">
      <button class="toolbar-btn primary" id="retryTestBtn">再测一次</button>
      <button class="toolbar-btn" id="backToTestHomeBtn">返回设置</button>
      <button class="toolbar-btn" id="backToReaderBtn">返回阅读</button>
    </div>`;

  $('#retryTestBtn').addEventListener('click', startTest);
  $('#backToTestHomeBtn').addEventListener('click', renderTestHome);
  $('#backToReaderBtn').addEventListener('click', () => {
    document.querySelector('[data-view="reader"]').click();
  });
}

window.addEventListener('beforeunload', () => {
  if (currentArticle && timerSeconds > 0) {
    navigator.sendBeacon('/api/reading/session', JSON.stringify({
      article_id: currentArticle.id, duration_seconds: timerSeconds,
      lookups: lookupsInSession, highlights: highlightsInSession
    }));
  }
});

// 页面加载时显示最新文章
async function loadLatestArticle() {
  try {
    const list = await api.get('/api/articles/?limit=1');
    if (list && list.length > 0) {
      const article = await api.get(`/api/articles/${list[0].id}`);
      currentArticle = article;
      renderArticle(article);
      $('#articleMeta').textContent = `${article.word_count} 词 · ${article.new_word_count} 生词`;
    }
  } catch(e) { console.log('加载最新文章失败', e); }
}
loadLatestArticle();

// 右侧查词历史滑出面板
const rightPanel = $('#rightPanel');
const rightPanelTrigger = $('#rightPanelTrigger');
let panelHoverTimer = null;
let panelPinned = false; // 工具栏按钮打开时固定显示

// 新手引导箭头：没触发过右侧抽屉才显示，首次把抽屉唤出后永久记住（不再打扰）
const guideArrow = $('#rightGuideArrow');
const GUIDE_SEEN_KEY = 'readloops_right_guide_seen';
if (guideArrow && !localStorage.getItem(GUIDE_SEEN_KEY)) {
  setTimeout(() => guideArrow.classList.add('show'), 900);
}
function dismissGuideArrow() {
  if (!guideArrow || guideArrow.classList.contains('dismiss')) return;
  guideArrow.classList.remove('show');
  guideArrow.classList.add('dismiss');
  localStorage.setItem(GUIDE_SEEN_KEY, '1');
}

// 鼠标移入右侧边缘触发（带防抖，避免快速划过误触）
rightPanelTrigger.addEventListener('mouseenter', () => {
  clearTimeout(panelHoverTimer);
  panelHoverTimer = setTimeout(() => {
    if (!panelPinned) {
      rightPanel.classList.add('show');
      loadLookupHistory();
      dismissGuideArrow(); // 用户已发现右侧抽屉，引导箭头退场
    }
  }, 120);
});

rightPanelTrigger.addEventListener('mouseleave', () => {
  clearTimeout(panelHoverTimer);
});

// 鼠标离开面板时收起（除非被固定）
rightPanel.addEventListener('mouseleave', () => {
  if (!panelPinned) {
    rightPanel.classList.remove('show');
  }
});

// 工具栏按钮切换（点击展开，离开侧页自动收回，和边缘触发逻辑一致）
$('#lookupHistoryBtn').addEventListener('click', () => {
  rightPanel.classList.add('show');
  loadLookupHistory();
  dismissGuideArrow();
});

async function loadLookupHistory() {
  try {
    const history = await api.get('/api/reading/lookups?limit=50');
    $('#lookupCount').textContent = history.length;
    if (history.length === 0) {
      $('#lookupHistoryList').innerHTML = '<div class="right-panel-empty">还没有查词记录<br>选中文字即可查词</div>';
      return;
    }
    $('#lookupHistoryList').innerHTML = history.map(item => `
      <div class="lookup-history-item" data-word="${escapeHtml(item.text)}" data-id="${item.word_id}">
        <div class="word-row">
          <span class="word">${escapeHtml(item.text)}</span>
          <span class="phonetic">${escapeHtml(item.phonetic)}</span>
        </div>
        <div class="meaning">${escapeHtml(item.meaning || '暂无释义')}</div>
        <div class="meta">
          <span>查${item.lookup_count}次 · ${new Date(item.last_lookup * 1000).toLocaleTimeString('zh-CN', {hour:'2-digit', minute:'2-digit'})}</span>
          <button class="add-btn" data-word="${escapeHtml(item.text)}" data-id="${item.word_id}">+ 生词本</button>
        </div>
      </div>
    `).join('');
    // 绑定加入生词本按钮
    $$('.lookup-history-item .add-btn').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const word = btn.dataset.word;
        await api.post('/api/words/add', { word });
        toast(`已添加「${word}」到生词本`);
        btn.textContent = '已添加';
        btn.disabled = true;
      });
    });
  } catch(e) { console.log('加载查词历史失败', e); }
}

// 查词后刷新历史（在原有的查词成功后调用）
const originalShowLookupPanel = showLookupPanel;
// 在查词成功后刷新历史（通过 MutationObserver 或直接在 lookup 成功后调用）
// 简化：每次面板显示后延迟刷新
const observer = new MutationObserver(() => {
  if (lookupPanel.classList.contains('show')) {
    setTimeout(loadLookupHistory, 500);
  }
});
observer.observe(lookupPanel, { attributes: true, attributeFilter: ['class'] });

// === 每日测试弹窗 ===
function checkDailyTestPrompt() {
  const today = new Date().toDateString();
  const lastPrompt = localStorage.getItem('readloops_last_test_prompt');
  const lastTest = localStorage.getItem('readloops_last_test_date');

  // 今天已经测试过，不弹窗
  if (lastTest === today) return;
  // 今天已经提示过，不重复弹窗
  if (lastPrompt === today) return;

  localStorage.setItem('readloops_last_test_prompt', today);
  showDailyTestModal();
}

function showDailyTestModal() {
  const modal = document.createElement('div');
  modal.className = 'modal-overlay';
  modal.id = 'dailyTestModal';
  modal.innerHTML = `
    <div class="modal-content daily-test-modal">
      <div class="modal-title">每日词汇测试</div>
      <div class="modal-body">
        <p>建议每天进行一次词汇测试，巩固阅读中遇到的生词。</p>
        <p style="color:var(--text-secondary);font-size:12px;margin-top:8px;">
          测试题目基于你的阅读数据智能生成，优先出你不会的词。
        </p>
      </div>
      <div class="modal-actions">
        <button class="toolbar-btn" id="skipTestBtn">稍后</button>
        <button class="toolbar-btn primary" id="startTestBtn">开始测试</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  modal.style.display = 'flex';

  document.getElementById('startTestBtn').addEventListener('click', () => {
    modal.remove();
    // 切换到测试页面
    $$('.nav-item').forEach(i => i.classList.remove('active'));
    document.querySelector('[data-view="test"]').classList.add('active');
    loadTest();
  });

  document.getElementById('skipTestBtn').addEventListener('click', () => {
    modal.remove();
  });

  modal.addEventListener('click', (e) => {
    if (e.target === modal) modal.remove();
  });
}

// 页面加载后延迟检查弹窗
setTimeout(checkDailyTestPrompt, 1500);

// ============================================================
// 知识库：书架 / 材料 / 知识图谱
// 三者串成一条链路：书架拿书 → 导入为材料 → 蒸馏出风格画像 → 图谱沉淀关联
// ============================================================

const TYPE_LABEL = { word: '词', phrase: '短语', article: '文章', material: '材料', book: '书' };

// ---------------------------------------------------------------- 书架

let libraryQuery = '';

async function loadLibrary() {
  const books = await api.get('/api/books');
  const cards = books.length === 0
    ? `<div class="kb-empty">书架还是空的。搜一本公版书试试——这里的书都可以合法下载与自由使用。</div>`
    : books.map(b => `
      <div class="kb-card" data-book-id="${b.id}">
        <div class="kb-card-main">
          <div class="kb-card-title">${escapeHtml(b.title)}</div>
          <div class="kb-card-meta">
            ${b.author ? escapeHtml(b.author) + ' · ' : ''}${b.word_count ? b.word_count.toLocaleString() + ' 词 · ' : ''}
            <span class="kb-badge kb-badge-${b.status}">${bookStatusText(b.status)}</span>
          </div>
        </div>
        <div class="kb-card-actions">
          ${b.status !== 'imported' ? `<button class="kb-mini-btn" data-act="import" data-id="${b.id}">导入材料</button>` : ''}
          <button class="kb-mini-btn kb-mini-danger" data-act="del" data-id="${b.id}">移除</button>
        </div>
      </div>`).join('');

  $('#reader').innerHTML = `
    <h1>书架</h1>
    <p class="kb-sub">收录公共领域书籍，可自由下载与使用。下载后点「导入材料」，就能参与蒸馏和出题。</p>

    <div class="kb-search">
      <input type="text" id="bookSearchInput" placeholder="搜索书名或作者，例如 alice / sherlock holmes" value="${escapeHtml(libraryQuery)}">
      <button class="toolbar-btn primary" id="bookSearchBtn">搜索</button>
    </div>
    <div id="bookSearchResults"></div>

    <div class="kb-section-title">我的书架 <span class="kb-count">${books.length}</span></div>
    <div class="kb-list">${cards}</div>
  `;

  $('#bookSearchBtn').addEventListener('click', doBookSearch);
  $('#bookSearchInput').addEventListener('keydown', e => {
    if (e.key === 'Enter') doBookSearch();
  });
  if (libraryQuery) doBookSearch();

  $$('.kb-card-actions .kb-mini-btn').forEach(btn => {
    btn.addEventListener('click', async e => {
      e.stopPropagation();
      const id = btn.dataset.id;
      if (btn.dataset.act === 'import') {
        btn.disabled = true;
        btn.textContent = '导入中…';
        const r = await api.post(`/api/books/${id}/import`, {});
        if (r.ok) {
          toast(`已导入，${r.word_count.toLocaleString()} 词`);
          loadLibrary();
        } else {
          toast(r.detail || r.error || '导入失败');
          btn.disabled = false;
          btn.textContent = '导入材料';
        }
      } else if (btn.dataset.act === 'del') {
        if (!await confirmDialog('确定从书架移除这本书吗？本地文件也会删除。', { danger: true })) return;
        await api.del(`/api/books/${id}`);
        toast('已移除');
        loadLibrary();
      }
    });
  });
}

function bookStatusText(s) {
  return { downloaded: '已下载', imported: '已导入', parsed: '已解析', failed: '失败' }[s] || s;
}

async function doBookSearch() {
  const q = $('#bookSearchInput').value.trim();
  libraryQuery = q;
  const box = $('#bookSearchResults');
  if (!q) { box.innerHTML = ''; return; }

  box.innerHTML = `<div class="kb-loading"><span class="loading"></span> 正在搜索…</div>`;
  let data;
  try {
    data = await api.get(`/api/books/search?q=${encodeURIComponent(q)}`);
  } catch (e) {
    box.innerHTML = `<div class="kb-empty">搜索失败：${escapeHtml(e.message || '书源不可达')}</div>`;
    return;
  }
  if (!data.results || data.results.length === 0) {
    box.innerHTML = `<div class="kb-empty">没找到相关书籍，换个关键词试试。</div>`;
    return;
  }

  box.innerHTML = `
    <div class="kb-section-title">搜索结果 <span class="kb-count">${data.results.length}</span></div>
    <div class="kb-list">
      ${data.results.map(b => `
        <div class="kb-card kb-card-result">
          <div class="kb-card-main">
            <div class="kb-card-title">${escapeHtml(b.title)}</div>
            <div class="kb-card-meta">
              ${b.author ? escapeHtml(b.author) + ' · ' : ''}${b.downloads ? b.downloads.toLocaleString() + ' 次下载' : ''}
            </div>
          </div>
          <div class="kb-card-actions">
            <button class="kb-mini-btn kb-mini-primary" data-act="dl"
                    data-sid="${b.source_id}" data-title="${escapeHtml(b.title)}"
                    data-author="${escapeHtml(b.author || '')}">下载</button>
          </div>
        </div>`).join('')}
    </div>
  `;

  $$('#bookSearchResults .kb-mini-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      btn.textContent = '下载中…';
      try {
        const r = await api.post('/api/books/download', {
          source_id: btn.dataset.sid,
          title: btn.dataset.title,
          author: btn.dataset.author,
        });
        toast(`已下载：${r.word_count.toLocaleString()} 词`);
        loadLibrary();
      } catch (e) {
        toast(e.message || '下载失败');
        btn.disabled = false;
        btn.textContent = '下载';
      }
    });
  });
}

// ---------------------------------------------------------------- 材料

async function loadMaterials() {
  const [items, profiles] = await Promise.all([
    api.get('/api/materials'),
    api.get('/api/materials/profiles/all'),
  ]);

  const list = items.length === 0
    ? `<div class="kb-empty">还没有材料。粘贴一段文本，或从书架导入一本书。</div>`
    : items.map(m => `
      <div class="kb-card" data-mid="${m.id}">
        <div class="kb-card-main">
          <div class="kb-card-title">${escapeHtml(m.title)}</div>
          <div class="kb-card-meta">
            ${m.parse_status === 'failed'
              ? `<span class="kb-badge kb-badge-failed">解析失败</span>`
              : `${(m.word_count || 0).toLocaleString()} 词 · ${m.sentence_count || 0} 句 · ${m.para_count || 0} 段`}
          </div>
          ${m.parse_status === 'failed' && m.error ? `<div class="kb-error">${escapeHtml(m.error)}</div>` : ''}
        </div>
        <div class="kb-card-actions">
          <label class="kb-check"><input type="checkbox" class="mat-pick" value="${m.id}"></label>
          <button class="kb-mini-btn kb-mini-danger" data-act="del" data-id="${m.id}">删除</button>
        </div>
      </div>`).join('');

  const profileCards = profiles.length === 0
    ? `<div class="kb-empty">还没有画像。勾选材料后点「蒸馏」生成。</div>`
    : profiles.map(p => `
      <div class="kb-card kb-card-profile ${p.is_active ? 'is-active' : ''}">
        <div class="kb-card-main">
          <div class="kb-card-title">${escapeHtml(p.name)}${p.is_active ? ' <span class="kb-badge kb-badge-active">生效中</span>' : ''}</div>
          <div class="kb-card-meta">${p.sample_count.toLocaleString()} 句 · 平均句长 ${(p.params.avg_len || 0)} 词 · 复合句 ${Math.round((p.params.compound_ratio || 0) * 100)}%</div>
        </div>
        <div class="kb-card-actions">
          ${p.is_active ? '' : `<button class="kb-mini-btn kb-mini-primary" data-act="activate" data-id="${p.id}">设为生效</button>`}
          <button class="kb-mini-btn kb-mini-danger" data-act="del-profile" data-id="${p.id}">删除</button>
        </div>
      </div>`).join('');

  $('#reader').innerHTML = `
    <h1>材料</h1>
    <p class="kb-sub">材料只留在本机，用于蒸馏与出题。材料越多，风格画像越准。</p>

    <div class="kb-panel">
      <div class="kb-panel-title">粘贴导入</div>
      <input type="text" id="matTitle" class="kb-input" placeholder="材料标题（可留空）">
      <textarea id="matContent" class="kb-textarea" placeholder="把英文文本粘贴到这里…"></textarea>
      <div class="kb-panel-actions">
        <button class="toolbar-btn primary" id="matPasteBtn">导入</button>
      </div>
    </div>

    <div class="kb-panel">
      <div class="kb-panel-title">从本地文件导入</div>
      <input type="text" id="matPath" class="kb-input" placeholder="文件绝对路径，支持 .txt / .md / .epub / .pdf">
      <div class="kb-panel-actions">
        <button class="toolbar-btn" id="matFileBtn">导入文件</button>
      </div>
      <div class="kb-hint">PDF 需要额外依赖：轻量用 <code>pip install pypdf</code>，扫描件/复杂版式建议 <code>pip install 'mineru[core]'</code>。</div>
    </div>

    <div class="kb-section-title">材料 <span class="kb-count">${items.length}</span></div>
    <div class="kb-list">${list}</div>

    <div class="kb-panel">
      <div class="kb-panel-title">蒸馏</div>
      <div class="kb-hint">勾选上面的材料（可多选），点蒸馏生成风格画像。多份材料会合并统计，分布更真实。</div>
      <div class="kb-panel-actions">
        <button class="toolbar-btn primary" id="distillBtn">蒸馏选中材料</button>
        <span class="kb-hint-inline" id="pickCount">已选 0 份</span>
      </div>
    </div>

    <div class="kb-section-title">风格画像 <span class="kb-count">${profiles.length}</span></div>
    <div class="kb-list">${profileCards}</div>
  `;

  const picks = $$('.mat-pick');
  const updatePick = () => {
    $('#pickCount').textContent = `已选 ${picks.filter(p => p.checked).length} 份`;
  };
  picks.forEach(p => p.addEventListener('change', updatePick));

  $('#matPasteBtn').addEventListener('click', async () => {
    const content = $('#matContent').value.trim();
    if (!content) { toast('请先粘贴内容'); return; }
    const r = await api.post('/api/materials/paste', {
      title: $('#matTitle').value.trim(), content,
    });
    toast(`已导入，${r.word_count.toLocaleString()} 词`);
    loadMaterials();
  });

  $('#matFileBtn').addEventListener('click', async () => {
    const path = $('#matPath').value.trim();
    if (!path) { toast('请填写文件路径'); return; }
    const r = await api.post('/api/materials/import', { path });
    if (r.ok) {
      toast(`已导入，${r.word_count.toLocaleString()} 词`);
      loadMaterials();
    } else {
      await showAlert(r.detail || r.error || '导入失败');
      loadMaterials();
    }
  });

  $('#distillBtn').addEventListener('click', async () => {
    const ids = picks.filter(p => p.checked).map(p => parseInt(p.value));
    if (!ids.length) { toast('请先勾选材料'); return; }
    const btn = $('#distillBtn');
    btn.disabled = true;
    btn.textContent = '蒸馏中…';
    try {
      const r = await api.post('/api/materials/distill', { material_ids: ids, activate: true });
      toast(`已生成画像，样本 ${r.params.sentence_count} 句`);
      loadMaterials();
    } catch (e) {
      toast(e.message || '蒸馏失败');
      btn.disabled = false;
      btn.textContent = '蒸馏选中材料';
    }
  });

  $$('.kb-card-actions .kb-mini-btn').forEach(btn => {
    btn.addEventListener('click', async e => {
      e.stopPropagation();
      const id = btn.dataset.id;
      if (btn.dataset.act === 'del') {
        if (!await confirmDialog('确定删除这份材料吗？', { danger: true })) return;
        await api.del(`/api/materials/${id}`);
        toast('已删除');
        loadMaterials();
      } else if (btn.dataset.act === 'activate') {
        await api.post(`/api/materials/profiles/${id}/activate`, {});
        toast('已设为生效画像');
        loadMaterials();
      } else if (btn.dataset.act === 'del-profile') {
        if (!await confirmDialog('确定删除这个画像吗？', { danger: true })) return;
        await api.del(`/api/materials/profiles/${id}`);
        toast('已删除');
        loadMaterials();
      }
    });
  });
}

// ---------------------------------------------------------------- 词汇量定级

// 定级测试的状态。分页勾选而不是逐词作答：194 道题逐个点要 300+ 次交互，
// 分页（每页 16 个）只要 13 次翻页，实测 3–4 分钟能做完。
let placementItems = [];
let placementKnown = null;     // Set：用户勾选「认识」的词
let placementPage = 0;
const PLACEMENT_PAGE_SIZE = 16;

async function loadPlacement() {
  const summary = await api.get('/api/placement/summary').catch(() => ({ has_result: false }));
  if (!summary.has_result) return renderPlacementIntro(null);
  renderPlacementResult(summary, true);
}

function renderPlacementIntro() {
  $('#reader').innerHTML = `
    <h1>词汇量定级</h1>
    <p class="kb-sub">先花 3–5 分钟测出你现在的词汇量。之后所有的覆盖率、考试缺口
       和选词，都以这次结果为起点。</p>
    <div class="placement-intro">
      <div class="placement-howto">
        <div class="placement-step"><b>1</b><span>逐页勾选你<b>认识</b>的单词，不确定的就别勾</span></div>
        <div class="placement-step"><b>2</b><span>词从最常用到最生僻分成 20 档，每档都抽了几道</span></div>
        <div class="placement-step"><b>3</b><span>里面混有少量<b>不存在的词</b>，用来校正自评偏高 —— 请照实作答</span></div>
        <div class="placement-step"><b>4</b><span>做完给出词汇量估计，以及离四级/六级/考研/雅思还差多少词</span></div>
      </div>
      <div class="placement-warn">
        这是<b>识别</b>测试：只要看到词能想起意思就算「认识」，不需要会拼写。
      </div>
      <div class="placement-actions">
        <button class="toolbar-btn primary large" id="placementStart">开始测试</button>
        <button class="toolbar-btn" onclick="switchPage(loadTest)">返回</button>
      </div>
    </div>
  `;
  $('#placementStart').addEventListener('click', startPlacement);
}

async function startPlacement() {
  $('#reader').innerHTML = '<div class="kb-loading">正在生成测试卷…</div>';
  const data = await api.get('/api/placement/items');
  placementItems = data.items || [];
  placementKnown = new Set();
  placementPage = 0;
  if (!placementItems.length) {
    $('#reader').innerHTML = '<h1>词汇量定级</h1><p class="kb-sub">题库为空，请先导入词库。</p>';
    return;
  }
  renderPlacementPage();
}

function renderPlacementPage() {
  const total = Math.ceil(placementItems.length / PLACEMENT_PAGE_SIZE);
  const start = placementPage * PLACEMENT_PAGE_SIZE;
  const pageItems = placementItems.slice(start, start + PLACEMENT_PAGE_SIZE);
  const isLast = placementPage >= total - 1;
  const pct = Math.round((start / placementItems.length) * 100);

  $('#reader').innerHTML = `
    <h1>词汇量定级</h1>
    <div class="placement-progress">
      <div class="placement-progress-bar"><span style="width:${pct}%"></span></div>
      <div class="placement-progress-text">第 ${placementPage + 1} / ${total} 页 · 已勾选 ${placementKnown.size} 个</div>
    </div>
    <div class="placement-hint">勾选你<b>认识</b>的词（看到能想起意思即可）</div>
    <div class="placement-grid">
      ${pageItems.map((it, i) => `
        <label class="placement-word ${placementKnown.has(it.text) ? 'known' : ''}" data-word="${escapeHtml(it.text)}">
          <input type="checkbox" ${placementKnown.has(it.text) ? 'checked' : ''}>
          <span>${escapeHtml(it.text)}</span>
        </label>`).join('')}
    </div>
    <div class="placement-nav">
      <button class="toolbar-btn" id="placementPrev" ${placementPage === 0 ? 'disabled' : ''}>上一页</button>
      <button class="toolbar-btn primary" id="placementNext">${isLast ? '提交并查看结果' : '下一页'}</button>
      <span class="placement-tip">不确定的不要勾 —— 混在里面的假词会揭穿猜测</span>
    </div>
  `;

  $$('.placement-word').forEach(el => {
    el.addEventListener('click', (e) => {
      e.preventDefault();
      const w = el.dataset.word;
      if (placementKnown.has(w)) { placementKnown.delete(w); el.classList.remove('known'); }
      else { placementKnown.add(w); el.classList.add('known'); }
      const cb = el.querySelector('input');
      if (cb) cb.checked = placementKnown.has(w);
      const counter = $('.placement-progress-text');
      if (counter) counter.textContent = `第 ${placementPage + 1} / ${total} 页 · 已勾选 ${placementKnown.size} 个`;
    });
  });

  const prev = $('#placementPrev');
  if (prev) prev.addEventListener('click', () => { if (placementPage > 0) { placementPage--; renderPlacementPage(); } });
  $('#placementNext').addEventListener('click', () => {
    if (!isLast) { placementPage++; renderPlacementPage(); }
    else submitPlacement();
  });
  $('#readerContainer').scrollTop = 0;
}

async function submitPlacement() {
  $('#reader').innerHTML = '<div class="kb-loading">正在计算…</div>';
  const answers = placementItems.map(it => ({
    text: it.text,
    known: placementKnown.has(it.text),
  }));
  try {
    const r = await api.post('/api/placement/submit', { answers });
    renderPlacementResult(r, false);
  } catch (err) {
    $('#reader').innerHTML = `<h1>词汇量定级</h1>
      <p class="kb-sub">提交失败：${escapeHtml(String(err.message || err))}</p>
      <button class="toolbar-btn" onclick="switchPage(loadPlacement)">返回</button>`;
  }
}

function renderPlacementResult(r, fromSummary) {
  const bands = r.bands || [];
  const est = r.vocab_estimate || 0;

  $('#reader').innerHTML = `
    <h1>词汇量定级</h1>
    <div class="placement-result">
      <div class="placement-score">
        <div class="placement-score-num">${est.toLocaleString()}${r.is_lower_bound ? '+' : ''}</div>
        <div class="placement-score-label">估计词汇量${r.is_lower_bound ? '（下界，最高档也基本掌握）' : ''}</div>
        ${fromSummary ? '' : `<div class="placement-score-meta">伪词虚报率 ${(r.false_alarm * 100).toFixed(1)}% · 作答 ${r.answered} 题
          <br><span class="placement-note">虚报率越低，这个估计越可信</span></div>`}
      </div>

      <div class="placement-chart">
        <div class="meter-title">分档掌握率（按词频从常用到生僻）</div>
        ${bands.map(b => `
          <div class="meter-row" title="${b.lo}–${b.hi} 名：抽 ${b.sampled} 题，认识 ${b.known} 题">
            <span class="meter-label">${(b.lo / 1000).toFixed(0)}k</span>
            <span class="meter-track">
              <span class="meter-fill" style="width:${Math.round(b.rate * 100)}%"></span>
            </span>
            <span class="meter-val">${Math.round(b.rate * 100)}%</span>
          </div>`).join('')}
      </div>

      <div class="placement-actions">
        <button class="toolbar-btn" id="placementRetake">重新测试</button>
        <button class="toolbar-btn" onclick="switchPage(loadStats)">去看统计</button>
      </div>
    </div>
  `;
  const sel = $('#targetExamSelect');
  if (sel) sel.addEventListener('change', async (e) => {
    await api.post('/api/stats/target-exam', { exam: e.target.value });
    toast(`目标考试已切换为「${e.target.selectedOptions[0].text}」`);
    loadStats();
  });

  const retake = $('#placementRetake');
  if (retake) retake.addEventListener('click', () => {
    if (confirm('重新测试会覆盖上一次的定级结果，继续？')) startPlacement();
  });
}

// ---------------------------------------------------------------- 知识图谱

let graphState = null;
let graphRAF = 0;
let graphResizeTimer = 0;

/**
 * 停掉物理循环（必须在重建 graphState 之前调用）。
 *
 * 这里原本有个很隐蔽的坑：循环标志 `_looping` 挂在 graphState 上，而
 * drawGraph() 每次都会重建 graphState —— 标志永远是 undefined，于是每切换
 * 一次筛选 / 每点一次重建，就永久多出一个 requestAnimationFrame 循环。
 * 多个循环同时迭代同一份节点，物理步进被叠加 N 倍
 * （实测：切换 4 次筛选后 30fps → 238fps），整张图疯抖不停。
 * 循环句柄必须是模块级的，且切换前先取消。
 */
function stopGraph() {
  if (graphRAF) { cancelAnimationFrame(graphRAF); graphRAF = 0; }
  graphState = null;
  const tip = $('#graphTip');
  if (tip) tip.style.display = 'none';
}

// 力导向参数。三条硬约束，缺一条图就会「炸开贴墙」：
//   1) 斥力必须软化并封顶。1/d² 在两点极近时是天文数字，节点会被瞬间弹飞，
//      撞到边界后永久贴墙抖动（实测修复前速度峰值 9651 px/帧）。
//   2) 必须按真实帧间隔归一化（dt）。掉帧时直接累加速度会让物理发散。
//   3) 边界要从「硬裁剪」改成「柔性回推」。硬裁剪把节点压在画布边上，
//      就是修复前上下两条硬边的直接成因。
const PHYS = {
  repulsion: 1800,    // 斥力系数（实测扫描出的最佳值，见调优记录）
  softMin2: 900,      // 距离平方下限（≈30px 内不再增强，消除奇点）
  maxRepForce: 24,    // 单对斥力上限
  springLen: 58,      // 弹簧自然长度
  springK: 0.075,
  centerPull: 0.0016,
  damping: 0.72,      // 每 1/60s 的阻尼
  maxSpeed: 6,        // 速度上限（px/帧，按 60fps 计）
  padding: 26,
  // ---- 退火（这是图能真正"停下来"的关键）----
  // 只靠阻尼是不够的：N 体斥力 + 弹簧的合力在多数布局里**没有平衡点**，
  // 系统会进入永久极限环（实测速度永远停在 0.1~0.3px/帧，永不收敛）。
  // 标准解法是模拟退火：让所有力乘一个从 1 衰减到 0 的 alpha，
  // 力本身趋于零，图必然静止（d3-force 的做法）。
  alphaDecay: 0.972,  // 每 1/60s 的 alpha 衰减
  alphaMin: 0.004,    // 低于此值即认为布局完成，停掉循环
};

// 四类节点的基准半径。要拉开层次，否则 78 个短语和 14 个词一样大，
// 视觉上就是一坨没有重心的点云。
// 尺寸分层要拉开：**用户的词**是主角，短语是语境（内置短语库来的，不是你自己的积累），
// 枢纽最显眼。之前短语比词还大，图上主体成了内置短语库，「不像我的图谱」。
const NODE_BASE_R = { word: 5.0, phrase: 2.6, article: 8.0, material: 10.0 };

async function loadGraph() {
  $('#reader').innerHTML = `
    <h1>知识图谱</h1>
    <p class="kb-sub">你的词、短语与文章连成一张网。点节点看关联，拖拽可以调整位置。</p>
    <div class="kb-toolbar">
      <button class="toolbar-btn" id="graphRebuildBtn">重建图谱</button>
      <div class="kb-filters" id="graphFilters">
        <label class="kb-check"><input type="checkbox" value="word" checked> 词</label>
        <label class="kb-check"><input type="checkbox" value="phrase" checked> 短语</label>
        <label class="kb-check"><input type="checkbox" value="article" checked> 文章</label>
        <label class="kb-check"><input type="checkbox" value="material" checked> 材料</label>
      </div>
      <span class="kb-hint-inline" id="graphStats"></span>
    </div>
    <div class="graph-stage" id="graphStage">
      <canvas id="graphCanvas"></canvas>
      <div class="graph-tip" id="graphTip"></div>
    </div>
  `;

  $('#graphRebuildBtn').addEventListener('click', async () => {
    const btn = $('#graphRebuildBtn');
    btn.disabled = true;
    btn.textContent = '重建中…';
    try {
      const s = await api.post('/api/graph/rebuild', {});
      toast(`图谱已重建：${s.nodes} 节点 / ${s.edges} 边`);
      await drawGraph();
    } catch (err) {
      toast('重建失败');
      console.error(err);
    } finally {
      btn.disabled = false;
      btn.textContent = '重建图谱';
    }
  });

  $$('#graphFilters input').forEach(cb => cb.addEventListener('change', drawGraph));

  await drawGraph();
}

async function drawGraph() {
  stopGraph();

  const types = $$('#graphFilters input').filter(c => c.checked).map(c => c.value);
  const canvas = $('#graphCanvas');
  const stage = $('#graphStage');
  if (!canvas || !stage) return;

  if (!types.length) {
    $('#graphStats').textContent = '请至少选择一种节点类型';
    canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height);
    return;
  }

  const data = await api.get(`/api/graph?types=${types.join(',')}&limit=400`);
  // 请求期间用户可能已经切走了 —— 元素没了就直接放弃，别往空气里画
  if (!document.body.contains(canvas)) return;
  $('#graphStats').textContent = `${data.nodes.length} 节点 · ${data.edges.length} 边`;

  if (!data.nodes.length) {
    $('#graphStats').textContent = '图谱是空的，点「重建图谱」试试';
    return;
  }

  const dpr = window.devicePixelRatio || 1;
  const W = stage.clientWidth, H = stage.clientHeight;
  canvas.width = W * dpr;
  canvas.height = H * dpr;
  canvas.style.width = W + 'px';
  canvas.style.height = H + 'px';
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  // ---- 物理域 vs 显示域（各向异性映射）----
  // 力导向的自然形状是圆的，而画布是横长条（1020×598）。直接在画布坐标里跑
  // 物理，图会被画布高度卡住、左右永远空一大片（实测 spanY≈1.00、spanX≈0.50）。
  // 所以物理只在 min(W,H) 的正方形域里跑，渲染/命中测试时再按画布长宽比
  // 拉伸到显示域 —— 布局变成椭圆，正好铺满可视区域。
  const PAD = PHYS.padding;
  const S = Math.max(80, Math.min(W, H) - PAD * 2);
  const kx = (W - PAD * 2) / S;
  const ky = (H - PAD * 2) / S;
  const toSX = (x) => PAD + (x - PAD) * kx;
  const toSY = (y) => PAD + (y - PAD) * ky;
  const toLX = (x) => PAD + (x - PAD) / kx;   // 屏幕 → 物理域（拖拽用）
  const toLY = (y) => PAD + (y - PAD) / ky;

  // 初始布局：按类型分环，避免全部堆在中心
  const typeOrder = ['article', 'material', 'phrase', 'word'];
  const nodes = data.nodes.map((n, i) => {
    const ring = Math.max(1, typeOrder.indexOf(n.type) + 1);
    const a = (i / data.nodes.length) * Math.PI * 2 + ring;
    const r = S * 0.10 * ring;
    const cx = PAD + S / 2, cy = PAD + S / 2;
    return {
      ...n,
      x: cx + Math.cos(a) * r,
      y: cy + Math.sin(a) * r,
      vx: 0, vy: 0,
      deg: 0,
    };
  });
  const byId = new Map(nodes.map(n => [n.id, n]));
  const links = data.edges
    .map(e => ({ s: byId.get(e.source), t: byId.get(e.target), w: e.weight, type: e.type }))
    .filter(l => l.s && l.t);
  links.forEach(l => { l.s.deg++; l.t.deg++; });

  // 兜底：万一出现孤立节点（没有弹簧牵引），给它在四周安排一个「家」并
  // 加强锚定。否则它只会被斥力一路推出去，最后贴在画布边缘排成硬边。
  const cx = PAD + S / 2, cy = PAD + S / 2;
  const isolated = nodes.filter(n => n.deg === 0);
  isolated.forEach((n, i) => {
    const a = (i / Math.max(1, isolated.length)) * Math.PI * 2 - Math.PI / 2;
    const r = S * 0.40;
    n.homeX = cx + Math.cos(a) * r;
    n.homeY = cy + Math.sin(a) * r;
    n.homeK = 0.06;
  });

  // 配色全部取自主题变量：写死颜色会在浅色主题下变成白字白底。
  const cssVar = (name, fallback) => {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name);
    return (v || '').trim() || fallback;
  };
  // 单色为主，而不是四个饱和色相。
  // 之前用 白/紫/青/橙 四色区分类别，四色平铺在深灰底上是最典型的「AI 生成仪表盘」
  // 观感；Obsidian 那类精致感来自克制 —— 一个前景色 + 明度层级。
  // 现在：类别的区分交给形状与大小，**信息用明度表达**（掌握度），
  // 只有 article/material 这类「枢纽」保留一点点色相。
  const text = cssVar('--text', '#fdfcfc');
  const accent = cssVar('--accent', '#8c82ff');
  const palette = {
    // 掌握度 → 明度：越亮表示掌握得越牢
    recalled: 0.92,
    recognized: 0.66,
    seen: 0.38,
    unknown: 0.22,
    // 枢纽节点（文章/材料）用一个去饱和的强调色，幅度很小
    hub: accent,
    hubAlpha: 0.72,
  };
  const ink = {
    label: text,
    edge: text,
  };
  // 光晕这个隐喻在深浅主题下是**反的**：深色底上「发光」= 亮核 + 扩散光；
  // 浅色底上同样的径向扩散看起来是「墨渍/糊掉」（实测暗色节点晕成一团）。
  // 所以浅色主题要收紧光晕、加大实心核，让节点是清晰的点而不是一团雾。
  const lightTheme = luminance(text) < 0.5;

  /** 粗略亮度（0=黑 1=白），用于判断当前是深色还是浅色主题。 */
  function luminance(color) {
    const c = (color || '').trim();
    let r = 200, g = 200, b = 200;
    if (c.startsWith('#')) {
      let hex = c.slice(1);
      if (hex.length === 3) hex = hex.split('').map(x => x + x).join('');
      const n = parseInt(hex, 16);
      if (!isNaN(n) && hex.length >= 6) {
        r = (n >> 16) & 255; g = (n >> 8) & 255; b = n & 255;
      }
    } else {
      const m = c.match(/rgba?\(([^)]+)\)/);
      if (m) { const p = m[1].split(',').map(x => parseFloat(x)); r = p[0]; g = p[1]; b = p[2]; }
    }
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
  }

  /** 给任意 CSS 颜色加 alpha（主题变量可能是 #rgb / #rrggbb / rgb()）。 */
  function withAlpha(color, a) {
    const c = (color || '').trim();
    if (c.startsWith('#')) {
      let hex = c.slice(1);
      if (hex.length === 3) hex = hex.split('').map(x => x + x).join('');
      const n = parseInt(hex, 16);
      if (!isNaN(n) && hex.length >= 6) {
        return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
      }
    }
    const m = c.match(/rgba?\(([^)]+)\)/);
    if (m) {
      const p = m[1].split(',').map(x => x.trim());
      return `rgba(${p[0]},${p[1]},${p[2]},${a})`;
    }
    return `rgba(200,200,200,${a})`;
  }

  graphState = {
    nodes, links, byId, ctx, canvas, W, H, S, kx, ky,
    toSX, toSY, toLX, toLY, palette, ink,
    drag: null, hover: null, selected: null,
    physics: true, last: 0, alpha: 1,
  };

  function tick(ts) {
    const st = graphState;
    if (!st) return;
    // 按真实帧间隔归一化，并**子步进**：
    // 无头浏览器 / 后台标签页的 rAF 会掉到 30fps，此时 dt=2，
    // 一步走两帧的距离会过冲 → 图抖动不收敛。拆成两个 dt/2 的子步。
    const raw = st.last ? Math.min(3, (ts - st.last) / 16.667) : 1;
    st.last = ts;
    const steps = raw > 1.25 ? 2 : 1;
    for (let i = 0; i < steps; i++) step(raw / steps);

    // 退火：力随时间衰减到 0 → 图必然收敛并静止
    st.alpha *= Math.pow(PHYS.alphaDecay, raw);
    if (st.alpha < PHYS.alphaMin) {
      st.alpha = 0;
      st.physics = false;
      fitToView();     // 停下来的那一刻把构图铺满画布
    }
  }

  /** 收敛后把整张图缩放到填满可用区域。
   *
   * 力导向稳定后的半径由斥力/弹簧的平衡决定，和画布大小无关 —— 结果常常是
   * 缩在中间一小团、四周大片空白（实测只占约一半宽度）。与其反复调物理参数
   * 去凑，不如收敛后做一次等比缩放：这是**参数无关**的构图保证。
   * 只在收敛时做一次，不干扰拖拽（拖拽改的是布局坐标）。
   */
  function fitToView() {
    const st = graphState;
    if (!st || st.nodes.length < 2) return;
    const pad = PHYS.padding + 8;
    const xs = st.nodes.map(n => n.x);
    const ys = st.nodes.map(n => n.y);
    const minX = Math.min(...xs), maxX = Math.max(...xs);
    const minY = Math.min(...ys), maxY = Math.max(...ys);
    const w = Math.max(1, maxX - minX), h = Math.max(1, maxY - minY);
    const avail = Math.max(40, st.S - pad * 2);
    // 上限 3.2 倍：节点很少时不至于把四五个点撑成一屏
    const scale = Math.min(avail / w, avail / h, 3.2);
    const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
    const tx = pad + st.S / 2, ty = pad + st.S / 2;
    for (const n of st.nodes) {
      n.x = tx + (n.x - cx) * scale;
      n.y = ty + (n.y - cy) * scale;
      if (n.homeX !== undefined) {
        n.homeX = tx + (n.homeX - cx) * scale;
        n.homeY = ty + (n.homeY - cy) * scale;
      }
    }
    render();
  }

  function step(dt) {
    const st = graphState;
    if (!st) return;
    const { nodes, links } = st;
    // 退火系数：所有力整体衰减（命名避开循环里的节点变量 a）
    const cool = st.alpha;
    const S = st.S, PAD = PHYS.padding;

    // 斥力（软化 + 封顶）
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j];
        const dx = b.x - a.x, dy = b.y - a.y;
        const d2 = Math.max(PHYS.softMin2, dx * dx + dy * dy);
        const d = Math.sqrt(d2);
        const f = Math.min(PHYS.maxRepForce, PHYS.repulsion / d2);
        const fx = (dx / d) * f, fy = (dy / d) * f;
        a.vx -= fx * cool; a.vy -= fy * cool;
        b.vx += fx * cool; b.vy += fy * cool;
      }
    }
    // 弹簧
    for (const l of links) {
      const dx = l.t.x - l.s.x, dy = l.t.y - l.s.y;
      const d = Math.max(1, Math.hypot(dx, dy));
      const f = (d - PHYS.springLen) * PHYS.springK * Math.min(2, l.w / 5);
      const fx = (dx / d) * f, fy = (dy / d) * f;
      l.s.vx += fx * cool; l.s.vy += fy * cool;
      l.t.vx -= fx * cool; l.t.vy -= fy * cool;
    }

    const damp = Math.pow(PHYS.damping, dt);
    for (const n of nodes) {
      // 向心（把整张图收在物理域中央）。
      // 注意：向心也必须乘 cool。否则退火后期斥力已经衰减到 0、向心力还在
      // 全强度工作，整张图会被一路勒到中心缩成一小团。所有力按同一系数衰减，
      // 布局形状才与 alpha 无关。
      n.vx += (PAD + S / 2 - n.x) * PHYS.centerPull * cool;
      n.vy += (PAD + S / 2 - n.y) * PHYS.centerPull * cool;
      // 孤立节点的环形锚定
      if (n.homeK) {
        n.vx += (n.homeX - n.x) * n.homeK * cool;
        n.vy += (n.homeY - n.y) * n.homeK * cool;
      }
      if (st.drag === n) { n.vx = 0; n.vy = 0; continue; }

      n.vx *= damp; n.vy *= damp;
      // 速度封顶：没有这一条，单帧位移能到几千像素，节点直接弹飞撞墙
      const sp = Math.hypot(n.vx, n.vy);
      if (sp > PHYS.maxSpeed) {
        n.vx = (n.vx / sp) * PHYS.maxSpeed;
        n.vy = (n.vy / sp) * PHYS.maxSpeed;
      }
      n.x += n.vx * dt; n.y += n.vy * dt;

      // 柔性边界：越界回推，而不是硬裁剪贴边
      if (n.x < PAD) { n.vx += (PAD - n.x) * 0.08 * cool; n.x = PAD; }
      else if (n.x > PAD + S) { n.vx -= (n.x - (PAD + S)) * 0.08 * cool; n.x = PAD + S; }
      if (n.y < PAD) { n.vy += (PAD - n.y) * 0.08 * cool; n.y = PAD; }
      else if (n.y > PAD + S) { n.vy -= (n.y - (PAD + S)) * 0.08 * cool; n.y = PAD + S; }
    }
  }

  function render() {
    const st = graphState;
    if (!st) return;
    const { ctx, W, H, nodes, links } = st;
    ctx.clearRect(0, 0, W, H);

    // 悬停时先把「邻居集合」算出来，用于整体降噪：
    // 非邻居压暗、邻居提亮 —— 这是 Obsidian 那种「聚焦」观感的来源。
    const near = new Set();
    if (st.hover) {
      near.add(st.hover);
      for (const l of links) {
        if (l.s === st.hover) near.add(l.t);
        else if (l.t === st.hover) near.add(l.s);
      }
    }
    const focusing = !!st.hover;

    // ---- 连线：曲线 + 极淡 ----
    // 直线边在 174 条时会织成一张网（「乱」的主因之一）；轻微弧线能显著降低这种
    // 机械感，也更容易看出哪条连着哪个节点。
    ctx.lineCap = 'round';
    for (const l of links) {
      const x1 = st.toSX(l.s.x), y1 = st.toSY(l.s.y);
      const x2 = st.toSX(l.t.x), y2 = st.toSY(l.t.y);
      const isNear = focusing && (near.has(l.s) && near.has(l.t));
      const dx = x2 - x1, dy = y2 - y1;
      // 垂直于连线方向外拱一点，拱度随距离缩放
      const bow = 0.09;
      const mx = (x1 + x2) / 2 + dy * bow;
      const my = (y1 + y2) / 2 - dx * bow;

      if (isNear) { ctx.globalAlpha = 0.42; ctx.lineWidth = 1.1; }
      else if (focusing) { ctx.globalAlpha = 0.045; ctx.lineWidth = 0.6; }
      else { ctx.globalAlpha = 0.085; ctx.lineWidth = 0.6; }
      ctx.strokeStyle = ink.edge;
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.quadraticCurveTo(mx, my, x2, y2);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;

    // ---- 节点：柔光 + 实心核 ----
    for (const n of nodes) {
      const x = st.toSX(n.x), y = st.toSY(n.y);
      const base = NODE_BASE_R[n.type] || 4;
      const degTerm = n.type === 'phrase' ? 0.7 : 1.35;   // 短语的度数增长也收敛些
      const r = base + Math.min(6, Math.sqrt(n.deg) * degTerm) + Math.min(3.5, n.weight * 0.3);
      const isHub = n.type === 'article' || n.type === 'material';
      const level = (n.meta && n.meta.m_level) || 'unknown';

      let alpha = isHub ? palette.hubAlpha : (palette[level] ?? palette.unknown);
      // 短语是语境而不是用户的积累，压暗一档让词成为视觉主体
      if (n.type === 'phrase') alpha *= 0.62;
      if (focusing) alpha = near.has(n) ? Math.min(1, alpha * 1.35) : alpha * 0.28;
      const color = isHub ? palette.hub : ink.label;

      // 柔光：径向渐变，中心亮、边缘透明。平涂圆看起来像调试散点图，
      // 有一圈光晕才有「漂浮的节点」那种质感。
      const glowR = r * (lightTheme ? (isHub ? 1.7 : 1.35) : (isHub ? 3.4 : 2.8));
      const grad = ctx.createRadialGradient(x, y, 0, x, y, glowR);
      const glowStrength = lightTheme ? alpha * 0.28 : alpha * 0.32;
      grad.addColorStop(0, withAlpha(color, Math.min(0.95, lightTheme ? alpha * 0.55 : alpha)));
      grad.addColorStop(0.42, withAlpha(color, glowStrength));
      grad.addColorStop(1, withAlpha(color, 0));
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.arc(x, y, glowR, 0, Math.PI * 2);
      ctx.fill();

      // 词 = 实心点；短语 = **空心环**。
      // 形状区分而不是颜色区分 —— 单色体系下才能既分得开又不花。
      // 环也贴合语义：短语是「由多个部分组成的单位」。
      const core = r * (lightTheme ? 0.82 : 0.62);
      if (n.type === 'phrase') {
        ctx.strokeStyle = withAlpha(color, Math.min(1, alpha * 1.15));
        ctx.lineWidth = Math.max(1, core * 0.34);
        ctx.beginPath();
        ctx.arc(x, y, core * 0.86, 0, Math.PI * 2);
        ctx.stroke();
      } else {
        ctx.fillStyle = withAlpha(color, Math.min(1, alpha * 1.05));
        ctx.beginPath();
        ctx.arc(x, y, core, 0, Math.PI * 2);
        ctx.fill();
      }

      if (n === st.hover) {
        ctx.lineWidth = 1.2;
        ctx.strokeStyle = withAlpha(ink.label, 0.9);
        ctx.beginPath();
        ctx.arc(x, y, r * 0.62 + 3, 0, Math.PI * 2);
        ctx.stroke();
      }

      // 记录屏幕半径，标签定位用
      n._r = r * (lightTheme ? 0.82 : 0.62);
      n._x = x; n._y = y;
    }

    // ---- 标签：默认几乎不画 ----
    // 「乱」的最大来源就是 60 个标签同时在线。Obsidian 默认只在悬停时显示，
    // 这里照做：常显的只有枢纽（文章/材料），且压得很淡。
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'center';

    const placed = [];
    const fits = (x, y, w, h) => {
      for (const p of placed) {
        if (Math.abs(p.x - x) < (p.w + w) / 2 && Math.abs(p.y - y) < (p.h + h) / 2) return false;
      }
      return true;
    };
    const drawLabel = (n, alpha, font, size) => {
      ctx.font = font;
      const text = n.label.length > 22 ? n.label.slice(0, 21) + '…' : n.label;
      const w = ctx.measureText(text).width;
      const y = n._y + n._r + 9;
      // 先试下方，被占就试上方，都不行就跳过 —— 宁可少标，也不要糊成一团
      for (const yy of [y, n._y - n._r - 9]) {
        if (fits(n._x, yy, w + 4, size + 3)) {
          placed.push({ x: n._x, y: yy, w: w + 4, h: size + 3 });
          ctx.fillStyle = withAlpha(ink.label, alpha);
          ctx.fillText(text, n._x, yy);
          return;
        }
      }
    };

    const SANS = 'ui-sans-serif, -apple-system, "PingFang SC", sans-serif';
    if (!focusing) {
      // 静止时只标一个枢纽。之前把 3 篇文章 + 1 份材料的标题全画出来，
      // 它们本来就挤在中心，四个标题叠在一起 —— 「乱」的最后一处来源。
      const hubs = nodes.filter(n => n.type === 'article' || n.type === 'material')
        .sort((a, b) => (b.deg + b.weight) - (a.deg + a.weight));
      if (hubs.length) drawLabel(hubs[0], 0.42, '10px ' + SANS, 10);
    } else {
      // 悬停：焦点优先，再按连接度排邻居 —— 信息量刚好，不糊。
      // ⚠️ 必须设上限：悬停「材料」这类高连接度的枢纽时，它的邻居就是全部短语，
      // 不封顶会一次画出 50+ 个标签，又回到「乱」。
      const ranked = [...near].filter(n => n._x !== undefined)
        .sort((a, b) => (b.deg + b.weight) - (a.deg + a.weight))
        .slice(0, 18);
      for (const n of ranked) {
        const hi = n === st.hover;
        drawLabel(n, hi ? 0.95 : 0.6, (hi ? '12px ' : '11px ') + SANS, hi ? 12 : 11);
      }
    }
  }

  function loop(ts) {
    graphRAF = 0;
    const st = graphState;
    if (!st) return;
    // 已经离开图谱页（canvas 被 innerHTML 换掉）→ 彻底停掉，不要空转
    if (!st.canvas.isConnected) { graphState = null; return; }
    if (st.physics) tick(ts);
    render();
    if (st.physics) graphRAF = requestAnimationFrame(loop);
  }

  // 唤醒：需要重新计算物理时传 true，只是重绘（悬停高亮）就不必
  function kick(physics) {
    const st = graphState;
    if (!st) return;
    if (physics) { st.physics = true; st.last = 0; st.alpha = Math.max(st.alpha, 0.6); }
    if (!graphRAF) graphRAF = requestAnimationFrame(loop);
  }

  // 命中测试：把屏幕坐标换回物理域再比距离
  function pick(sx, sy) {
    const st = graphState;
    if (!st) return null;
    const lx = st.toLX(sx), ly = st.toLY(sy);
    let best = null, bestD = 400;
    for (const n of st.nodes) {
      const d = (n.x - lx) ** 2 + (n.y - ly) ** 2;
      if (d < bestD) { bestD = d; best = n; }
    }
    return best;
  }

  function localPos(e) {
    const r = canvas.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  }

  canvas.onmousemove = (e) => {
    const st = graphState;
    if (!st) return;
    const [x, y] = localPos(e);
    if (st.drag) {
      st.drag.x = st.toLX(x); st.drag.y = st.toLY(y);
      kick(true);
      return;
    }
    const hit = pick(x, y);
    if (hit !== st.hover) { st.hover = hit; kick(false); }
    canvas.style.cursor = hit ? 'pointer' : 'default';
    const tip = $('#graphTip');
    if (!tip) return;
    if (hit) {
      // 固定在舞台左下角，不跟随鼠标。
      // 画布上悬停时已经会显示名字，跟随鼠标的浮层会直接压在标签上（实测重叠）。
      tip.style.display = 'block';
      tip.style.left = '18px';
      tip.style.top = 'auto';
      tip.style.bottom = '18px';
      const m = hit.meta || {};
      const ML = { recalled: '想得起来', recognized: '认得出来', seen: '见过面', unknown: '未接触' };
      tip.innerHTML = `<b>${escapeHtml(hit.label)}</b>` +
        `<div class="tip-sub">${TYPE_LABEL[hit.type] || hit.type}` +
        (m.m_level ? ` · ${ML[m.m_level] || m.m_level}` : '') +
        (m.level ? ` · ${escapeHtml(m.level)}` : '') + '</div>' +
        (m.meaning ? `<div class="tip-body">${escapeHtml(String(m.meaning).slice(0, 90))}</div>` : '');
    } else {
      tip.style.display = 'none';
    }
  };

  canvas.onmousedown = (e) => {
    const st = graphState;
    if (!st) return;
    const [x, y] = localPos(e);
    const hit = pick(x, y);
    if (hit) { st.drag = hit; st.selected = hit; kick(true); }
  };

  canvas.onmouseup = () => {
    const st = graphState;
    if (st && st.drag) { st.drag = null; kick(true); }
  };

  canvas.onmouseleave = () => {
    const st = graphState;
    if (!st) return;
    st.hover = null;
    kick(false);
    const tip = $('#graphTip');
    if (tip) tip.style.display = 'none';
  };

  graphRAF = requestAnimationFrame(loop);
}

// 容器尺寸变化（窗口缩放 / 侧边栏折叠）后重新定尺寸。
// 只注册一次，避免每次进图谱页都叠加监听。
window.addEventListener('resize', () => {
  if (!graphState) return;
  clearTimeout(graphResizeTimer);
  graphResizeTimer = setTimeout(() => { if (graphState) drawGraph(); }, 220);
});
